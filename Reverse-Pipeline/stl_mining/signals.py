"""
FILE: stl_mining/signals.py
PURPOSE: The ETL (Extract, Transform, Load) Layer for Tool-Only Surgical STL.
         Responsibility:
         1. Ingest raw tool logs from the Trace Logger.
         2. Clean, Interpolate, and Normalize tool signals.
         3. Generate 'Synthetic Signals' (smoothed booleans, tool risk contexts).
         4. Provide a structured SignalTrace object to the Miner.

ARCHITECTURE:
- SignalTrace: Immutable data structure holding the timeline.
- TraceLoader: Factory for parsing raw CSVs/Dataframes.
- SignalProcessor: Static utility suite for vector math.
- SyntheticSignalFactory: Derives complex multi-tool states.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Union, Callable
from scipy.interpolate import interp1d

# Constants
DEFAULT_FPS = 25.0
TOOL_COUNT = 7  # 0:Grasper, 1:Bipolar, 2:Hook, 3:Scissors, 4:Clipper, 5:Irrigator, 6:Bag

class SignalTrace:
    """
    The canonical data structure for the STL Miner.
    Holds a dictionary of signals aligned to a common time axis.
    """
    def __init__(self, signals: Dict[str, np.array], times: np.array):
        self._signals = signals
        self._times = times
        self._length = len(times)
        
        # Validation
        for name, sig in signals.items():
            if len(sig) != self._length:
                raise ValueError(f"Signal '{name}' length ({len(sig)}) does not match time length ({self._length})")

    @property
    def signal_names(self) -> List[str]:
        return list(self._signals.keys())

    @property
    def times(self) -> np.array:
        return self._times

    def __getitem__(self, key: str) -> np.array:
        if key not in self._signals:
            raise KeyError(f"Signal '{key}' not found in trace. Available: {self.signal_names}")
        return self._signals[key]

    def add_signal(self, name: str, data: np.array):
        """Injects a new synthetic signal into the trace."""
        if len(data) != self._length:
            raise ValueError(f"New signal length {len(data)} != Trace length {self._length}")
        self._signals[name] = data

    def to_dataframe(self) -> pd.DataFrame:
        """Export back to Pandas for debugging/visualization."""
        df = pd.DataFrame(self._signals)
        df['time'] = self._times
        return df


class SignalProcessor:
    """
    Vectorized utilities for signal conditioning.
    Used to clean up noisy AI outputs before Formal Verification.
    """
    
    @staticmethod
    def smooth_boolean(signal: np.array, window_size: int = 5) -> np.array:
        """
        Applies a Majority Vote or 'Hysteresis' filter to a 0/1 signal.
        Removes single-frame flickers which would otherwise break strict logic templates.
        """
        if len(signal) == 0:
            return signal
            
        # Convolution with a uniform kernel
        kernel = np.ones(window_size) / window_size
        convolved = np.convolve(signal, kernel, mode='same')
        
        # Re-binarize: If > 0.5 (majority), set to 1.0
        return (convolved > 0.5).astype(np.float64)

    @staticmethod
    def compute_derivative(signal: np.array, dt: float) -> np.array:
        """
        Calculates d/dt (Rate of Change).
        Useful for continuous metrics or measuring transition speeds.
        """
        if len(signal) < 2:
            return np.zeros_like(signal)
        grad = np.gradient(signal, dt)
        return grad

    @staticmethod
    def normalize(signal: np.array, mode: str = 'minmax') -> np.array:
        """
        Normalizes continuous signals to [0,1] range.
        """
        if mode == 'minmax':
            min_v, max_v = np.min(signal), np.max(signal)
            if max_v - min_v == 0: return np.zeros_like(signal)
            return (signal - min_v) / (max_v - min_v)
        return signal


class TraceLoader:
    """
    Factory class to load CSV logs and convert them into SignalTrace objects.
    Handles the parsing logic specific to your tool-only 'trace_logger.py'.
    """
    
    def __init__(self, fps: float = DEFAULT_FPS):
        self.fps = fps

    def load_csv(self, csv_path: str) -> SignalTrace:
        """
        Parses 'ideal_surgery_trace.csv'.
        Expected Columns: frame, active_tools
        """
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            raise IOError(f"Failed to read CSV at {csv_path}: {e}")

        # 1. Base Time Axis
        if 'frame' not in df.columns:
            raise ValueError("CSV missing required 'frame' column.")
        
        times = df['frame'].values / self.fps
        signals = {}

        # 2. Parse Tool Strings ("1|4|5" -> tool_1=1.0, tool_4=1.0, tool_5=1.0)
        # Vectorized parsing for speed
        if 'active_tools' in df.columns:
            # Initialize empty tool signals
            for t_id in range(TOOL_COUNT):
                signals[f'tool_{t_id}'] = np.zeros(len(times), dtype=np.float64)
            
            # Populate signals based on the pipe-delimited string
            for idx, row in df.iterrows():
                active_str = str(row['active_tools'])
                if not active_str or active_str.lower() == 'nan':
                    continue
                
                # Split "1|4" -> ['1', '4']
                ids = active_str.split('|')
                for t_str in ids:
                    if t_str.strip().isdigit():
                        t_id = int(t_str)
                        if 0 <= t_id < TOOL_COUNT:
                            signals[f'tool_{t_id}'][idx] = 1.0
        else:
            raise ValueError("CSV missing 'active_tools' column. Ensure trace_logger.py is generating correctly.")
        
        # 3. Apply Smoothing to Tool Signals (Critical for Logic Mining)
        # Raw AI flickers; logic needs absolute stability.
        print("   [SignalProcessor] Smoothing tool detection signals...")
        for t_id in range(TOOL_COUNT):
            raw_sig = signals[f'tool_{t_id}']
            signals[f'tool_{t_id}'] = SignalProcessor.smooth_boolean(raw_sig, window_size=5)

        return SignalTrace(signals, times)


class SyntheticSignalFactory:
    """
    Advanced Module: Creates complex derived signals for deeper tool-mining.
    This allows us to mine rules based on combinations of tools acting together.
    """
    
    @staticmethod
    def create_safety_context(trace: SignalTrace) -> SignalTrace:
        """
        Adds signals representing unsafe or specific multi-tool states.
        """
        # 1. Hook without Grasper (Traction Check)
        # Safe dissection usually requires traction.
        # Risk = Hook(2) AND NOT Grasper(0)
        tool_2 = trace['tool_2']
        tool_0 = trace['tool_0']
        
        # Note: 1.0 - tool_0 acts as NOT(tool_0)
        risk_traction_hook = tool_2 * (1.0 - tool_0)
        trace.add_signal('hook_no_traction', risk_traction_hook)

        # 2. Clipper without Grasper
        # Similar to above, clipping often requires the gallbladder to be retracted.
        tool_4 = trace['tool_4']
        risk_traction_clip = tool_4 * (1.0 - tool_0)
        trace.add_signal('clipper_no_traction', risk_traction_clip)
        
        # 3. Overcrowding Signal (More than 3 tools in frame)
        # Calculate sum of all tool arrays across the time axis
        tool_signals = [trace[f'tool_{i}'] for i in range(TOOL_COUNT)]
        total_active = np.sum(tool_signals, axis=0)
        
        # Binary signal: 1 if > 3 tools active, else 0
        overcrowded = (total_active > 3).astype(np.float64)
        trace.add_signal('scene_overcrowded', overcrowded)
        
        return trace
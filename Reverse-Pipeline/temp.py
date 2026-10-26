"""
FILE: stl_mining/signals.py
PURPOSE: The ETL Layer for Tool STL Mining.
         Ingests raw CSV logs, parses tool IDs, and applies temporal smoothing.
"""

import pandas as pd
import numpy as np
from typing import Dict, List

DEFAULT_FPS = 25.0
TOOL_COUNT = 7  # Grasper, Bipolar, Hook, Scissors, Clipper, Irrigator, Bag

class SignalTrace:
    def __init__(self, signals: Dict[str, np.array], times: np.array):
        self._signals = signals
        self._times = times
        self._length = len(times)
        
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
            raise KeyError(f"Signal '{key}' not found.")
        return self._signals[key]

class SignalProcessor:
    @staticmethod
    def smooth_boolean(signal: np.array, window_size: int = 5) -> np.array:
        """Applies Majority Vote Hysteresis to remove AI tracking flicker."""
        kernel = np.ones(window_size) / window_size
        convolved = np.convolve(signal, kernel, mode='same')
        return (convolved > 0.5).astype(np.float64)

class TraceLoader:
    def __init__(self, fps: float = DEFAULT_FPS):
        self.fps = fps

    def load_csv(self, csv_path: str) -> SignalTrace:
        df = pd.read_csv(csv_path)
        
        if 'frame' not in df.columns:
            raise ValueError("CSV missing required 'frame' column.")
        
        times = df['frame'].values / self.fps
        signals = {}

        # 1. Parse Tool Strings
        for t_id in range(TOOL_COUNT):
            signals[f'tool_{t_id}'] = np.zeros(len(times), dtype=np.float64)
            
        if 'active_tools' in df.columns:
            for idx, row in df.iterrows():
                active_str = str(row['active_tools'])
                if not active_str or active_str.lower() == 'nan':
                    continue
                
                ids = active_str.split('|')
                for t_str in ids:
                    if t_str.strip().isdigit():
                        t_id = int(t_str)
                        if 0 <= t_id < TOOL_COUNT:
                            signals[f'tool_{t_id}'][idx] = 1.0
        
        # 2. Smooth Tool Signals
        print("   [SignalProcessor] Smoothing tool detections...")
        for t_id in range(TOOL_COUNT):
            signals[f'tool_{t_id}'] = SignalProcessor.smooth_boolean(signals[f'tool_{t_id}'], window_size=5)

        return SignalTrace(signals, times)
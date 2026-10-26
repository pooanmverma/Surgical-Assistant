"""
FILE: stl_mining/core.py
PURPOSE: The Mathematical Kernel of the STL Engine (Tool-Only Workflow).
         Defines the Abstract Syntax Tree (AST) for Signal Temporal Logic.
         Implements Vectorized Robustness Semantics (Donze et al.) for Tool Signals.

CONCEPTS:
- Trace: A dictionary of tool signals {e.g., 'tool_0': np.array([0.0, 1.0, 1.0, ...])}
- Robustness (rho): Positive = Satisfied (Safe), Negative = Violated (Unsafe).
- Operators:
    - Predicate: tool_x > 0.5 (Is the tool active?)
    - Not (~): Negation
    - And (&): Min
    - Or (|): Max
    - Always (Box): Sliding Window Min (Tool must stay active)
    - Eventually (Diamond): Sliding Window Max (Tool will eventually appear)
    - Historically (H): Past verification (Tool was used previously)
    - Until (U): Temporal interdependence (Tool A active until Tool B)
"""

import numpy as np
from abc import ABC, abstractmethod
from typing import Dict, Tuple, Union

# Use standard float precision for GPU/CPU math compatibility
DTYPE = np.float64

class STLFormula(ABC):
    """Abstract Base Class for all STL Nodes in the AST."""
    
    @abstractmethod
    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        """
        Compute the robustness signal over time.
        Args:
            trace: Dict mapping signal names (e.g. 'tool_4') to numpy arrays of values.
            times: Monotonically increasing time points corresponding to signal samples.
        Returns:
            rho: Numpy array of robustness values (same length as times).
        """
        pass

    def __invert__(self):
        return Negation(self)

    def __and__(self, other):
        return Conjunction(self, other)

    def __or__(self, other):
        return Disjunction(self, other)

class Predicate(STLFormula):
    """
    Leaf Node. Represents a constraint on a single signal: f(x) > c.
    In the tool workflow, this usually checks if tool confidence > 0.5.
    Robustness rho = x(t) - c (if operator is >)
    """
    def __init__(self, signal: str, operator: str, threshold: float):
        self.signal = signal
        self.operator = operator
        self.threshold = threshold

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        if self.signal not in trace:
            raise KeyError(f"Signal '{self.signal}' not found in trace database.")
        
        signal_val = trace[self.signal].astype(DTYPE)
        
        # rho(x > c) = x - c
        # rho(x < c) = c - x
        if self.operator in ['>', '>=']:
            return signal_val - self.threshold
        elif self.operator in ['<', '<=']:
            return self.threshold - signal_val
        elif self.operator == '==':
            # rho = -|x - c|
            return -np.abs(signal_val - self.threshold)
        else:
            raise ValueError(f"Unknown mathematical operator: {self.operator}")

    def __repr__(self):
        return f"({self.signal} {self.operator} {self.threshold:.2f})"

class Negation(STLFormula):
    """rho(not phi) = -rho(phi)"""
    def __init__(self, phi: STLFormula):
        self.phi = phi

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        return -self.phi.robustness(trace, times)

    def __repr__(self):
        return f"¬({self.phi})"

class Conjunction(STLFormula):
    """rho(phi1 and phi2) = min(rho(phi1), rho(phi2))"""
    def __init__(self, phi1: STLFormula, phi2: STLFormula):
        self.phi1 = phi1
        self.phi2 = phi2

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        r1 = self.phi1.robustness(trace, times)
        r2 = self.phi2.robustness(trace, times)
        return np.minimum(r1, r2)

    def __repr__(self):
        return f"({self.phi1} ∧ {self.phi2})"

class Disjunction(STLFormula):
    """rho(phi1 or phi2) = max(rho(phi1), rho(phi2))"""
    def __init__(self, phi1: STLFormula, phi2: STLFormula):
        self.phi1 = phi1
        self.phi2 = phi2

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        r1 = self.phi1.robustness(trace, times)
        r2 = self.phi2.robustness(trace, times)
        return np.maximum(r1, r2)

    def __repr__(self):
        return f"({self.phi1} ∨ {self.phi2})"

class Implication(STLFormula):
    """A implies B  ==  (Not A) or B. Crucial for IF-THEN workflow rules."""
    def __init__(self, antecedent: STLFormula, consequent: STLFormula):
        self.phi = Disjunction(Negation(antecedent), consequent)

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        return self.phi.robustness(trace, times)

    def __repr__(self):
        return f"({self.phi.phi1.phi} ⇒ {self.phi.phi2})"

# --- TEMPORAL OPERATORS (The Heavy Lifters) ---

def sliding_window_op(robustness_trace: np.array, times: np.array, 
                      interval: Tuple[float, float], op_func) -> np.array:
    """
    Vectorized sliding window calculation for temporal operators.
    Args:
        op_func: np.min for Always, np.max for Eventually
    """
    a, b = interval
    n = len(times)
    result = np.zeros(n, dtype=DTYPE)
    
    # Pre-compute time limits for efficiency
    # Optimization: If interval is [0, inf), it's global suffix
    if b == np.inf and a == 0:
        if op_func == np.min:
            # Accumulated min from right to left
            return np.minimum.accumulate(robustness_trace[::-1])[::-1]
        else:
            return np.maximum.accumulate(robustness_trace[::-1])[::-1]

    # General Case: Windowed
    for i in range(n):
        t = times[i]
        start_t = t + a
        end_t = t + b
        
        # Find slice indices (O(log N) binary search via numpy)
        idx_start = np.searchsorted(times, start_t, side='left')
        idx_end = np.searchsorted(times, end_t, side='right')
        
        if idx_start >= n:
            # Window is completely in future beyond trace
            # Boundary condition: assume constant last value
            result[i] = robustness_trace[-1]
        else:
            # Clip idx_end
            idx_end = min(idx_end, n)
            
            if idx_start == idx_end:
                # Window falls between samples or at end
                result[i] = robustness_trace[-1] 
            else:
                window = robustness_trace[idx_start:idx_end]
                result[i] = op_func(window)
                
    return result

class Always(STLFormula):#For EVERY frame IF Scissors is active THEN Grasper must also be active
    """
    Globally (Box) Operator.
    Rule holds if rho is positive for ALL times in the interval.
    rho = min(rho(t')) for t' in [t+a, t+b]
    """
    def __init__(self, phi: STLFormula, interval: Tuple[float, float] = (0, np.inf)):
        self.phi = phi
        self.interval = interval

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        inner_rho = self.phi.robustness(trace, times)
        return sliding_window_op(inner_rho, times, self.interval, np.min)

    def __repr__(self):
        return f"◻_[{self.interval[0]},{self.interval[1]}] ({self.phi})"

class Eventually(STLFormula):
    """
    Future (Diamond) Operator.
    Rule holds if rho is positive for AT LEAST ONE time in interval.
    rho = max(rho(t')) for t' in [t+a, t+b]
    """
    def __init__(self, phi: STLFormula, interval: Tuple[float, float] = (0, np.inf)):
        self.phi = phi
        self.interval = interval

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        inner_rho = self.phi.robustness(trace, times)
        return sliding_window_op(inner_rho, times, self.interval, np.max)

    def __repr__(self):
        return f"♢_[{self.interval[0]},{self.interval[1]}] ({self.phi})"

class Historically(STLFormula):
    """
    Past Operator. "It has always been the case that..."
    Useful for: "If Tool B is active, Tool A MUST HAVE appeared before."
    """
    def __init__(self, phi: STLFormula, interval: Tuple[float, float] = (0, np.inf)):
        self.phi = phi
        self.a, self.b = interval

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        inner_rho = self.phi.robustness(trace, times)
        res = np.zeros_like(inner_rho)
        n = len(times)
        
        # Look backwards
        for i in range(n):
            t = times[i]
            # Window: [t-b, t-a]
            start_t = t - self.b
            end_t = t - self.a
            
            idx_start = np.searchsorted(times, start_t, side='left')
            idx_end = np.searchsorted(times, end_t, side='right')
            
            if idx_end <= 0:
                res[i] = inner_rho[0] # Boundary start
            else:
                idx_start = max(0, idx_start)
                window = inner_rho[idx_start:idx_end]
                if len(window) == 0:
                    res[i] = inner_rho[0]
                else:
                    res[i] = np.min(window)
        return res

    def __repr__(self):
        return f"H_[{self.a},{self.b}] ({self.phi})"

class Until(STLFormula):
    """
    Strict Until Operator. "Tool A remains active UNTIL Tool B becomes active."
    Complex robustness metric: max_{t'} ( min( rho(B, t'), min_{t'' in [t, t']} rho(A, t'') ) )
    """
    def __init__(self, phi1: STLFormula, phi2: STLFormula, interval: Tuple[float, float] = (0, np.inf)):
        self.phi1 = phi1  # The condition that must hold (e.g., Grasper)
        self.phi2 = phi2  # The terminating condition (e.g., Clipper)
        self.a, self.b = interval

    def robustness(self, trace: Dict[str, np.array], times: np.array) -> np.array:
        r1 = self.phi1.robustness(trace, times)
        r2 = self.phi2.robustness(trace, times)
        res = -np.inf * np.ones_like(r1)

        for i, t in enumerate(times):
            start_time = t + self.a
            end_time = t + self.b
            
            idx_start = np.searchsorted(times, start_time, side='left')
            idx_end = np.searchsorted(times, end_time, side='right')
            
            best_score = -np.inf
            
            # Iterate through possible "switch times" t'
            for k in range(idx_start, min(idx_end, len(times))):
                # At time k, phi2 must hold
                score_2 = r2[k]
                
                # For all times from i to k, phi1 must hold
                score_1 = np.min(r1[i:k+1])
                
                # Combine
                min_score = min(score_1, score_2)
                best_score = max(best_score, min_score)
            
            res[i] = best_score
            
        return res

    def __repr__(self):
        return f"({self.phi1} U_[{self.a},{self.b}] {self.phi2})"
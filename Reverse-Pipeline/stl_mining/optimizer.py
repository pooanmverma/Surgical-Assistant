"""
FILE: stl_mining/optimizer.py
PURPOSE: The "Solver" Engine for STL Specification Mining.
         
         ALGORITHM:
         - Uses Monotonic Binary Search to find optimal temporal bounds.
         - Input: Signal Trace (Tools) + Parametric STL Template.
         - Output: The tightest parameter values that satisfy the expert's trace.
         
         MATHEMATICAL GUARANTEE:
         - Finds the exact boundary between Satisfied (rho >= 0) and Violated (rho < 0)
           within the specified step-size precision.
"""

import numpy as np
from typing import Dict, List, Any, Tuple, Optional
import logging
from .core import STLFormula
from .templates import STLTemplate, ParameterSpace

logger = logging.getLogger("STLOptimizer")

class STLOptimizer:
    """
    Mines optimal parameters for STL templates given a continuous signal trace.
    Built for tool-workflow mining where signals represent tool active states (0.0 to 1.0).
    """
    def __init__(self, trace: Dict[str, np.array], times: np.array):
        if not trace or len(times) == 0:
            raise ValueError("Optimizer requires a non-empty signal trace and time vector.")
            
        self.trace = trace
        self.times = times

    def verify(self, formula: STLFormula) -> float:
        """
        Computes the minimum robustness of a formula over the entire trace.
        rho_min = min_t rho(phi, trace, t)
        
        Returns:
            float: Positive if satisfied globally, Negative if violated at any point.
        """
        rob_trace = formula.robustness(self.trace, self.times)
        
        # In a perfectly safe video, the minimum robustness across all frames must be >= 0
        return float(np.min(rob_trace))

    def optimize(self, template: STLTemplate, trace_stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Main entry point. Optimizes a template against the surgical trace.
        
        Strategy:
        0. If 0 parameters: Strict boolean verification (e.g., Mutual Exclusion).
        1. If 1 parameter: O(log N) Monotonic Binary Search.
        2. If 2 parameters: Grid Search on secondary, Binary Search on primary.
        """
        params_def = template.get_search_space(trace_stats)
        
        # Case 0: Parameter-less templates (Strict Rules)
        if not params_def:
            logger.debug(f"Template {template.name} has no parameters. Performing strict verification.")
            phi = template.generate_formula()
            rho = self.verify(phi)
            
            if rho >= 0:
                return {
                    "name": template.name,
                    "description": template.explain(),
                    "parameters": {},
                    "robustness": rho
                }
            return None

        # Case 1: Single Parameter Optimization
        if len(params_def) == 1:
            primary_param = params_def[0]
            logger.info(f"Optimizing {template.name} (Param: {primary_param.name})...")
            
            best_val, best_rho = self._binary_search(template, primary_param, {})
            
            if best_val is not None:
                return {
                    "name": template.name,
                    "description": template.explain(**{primary_param.name: best_val}),
                    "parameters": {primary_param.name: best_val},
                    "robustness": best_rho
                }
            else:
                logger.debug(f"   -> Failed to find valid parameter bound for {template.name}")
                return None

        # Case 2: Multi-Parameter Optimization (e.g., Confidence + Deadline)
        elif len(params_def) == 2:
            primary_param = params_def[0]
            secondary_param = params_def[1]
            logger.info(f"Multi-Optimizing {template.name} ({primary_param.name}, {secondary_param.name})...")
            
            best_params, best_rho = self._grid_binary_search(template, primary_param, secondary_param)
            
            if best_params is not None:
                return {
                    "name": template.name,
                    "description": template.explain(**best_params),
                    "parameters": best_params,
                    "robustness": best_rho
                }
            return None
            
        else:
            logger.error(f"Template {template.name} has >2 parameters. Optimization not supported.")
            return None


    def _binary_search(self, template: STLTemplate, param_def: ParameterSpace, 
                       fixed_params: Dict[str, float]) -> Tuple[Optional[float], float]:
        """
        Performs Binary Search to find the boundary of satisfaction.
        Exploits the monotonicity of the STL formula to run in logarithmic time.
        """
        low = param_def.min_val
        high = param_def.max_val
        step = param_def.step
        monotonicity = param_def.monotonicity
        
        best_valid_param = None
        best_rho = -np.inf
        
        # Validation Check: Does the easiest possible value satisfy the trace?
        # If 'increasing', Max is easiest. If 'decreasing', Min is easiest.
        easiest_val = high if monotonicity == 'increasing' else low
        
        params = fixed_params.copy()
        params[param_def.name] = easiest_val
        phi = template.generate_formula(**params)
        rho = self.verify(phi)
        
        if rho < 0:
            logger.debug(f"   Rule violated even at easiest bound ({easiest_val}). rho={rho:.4f}")
            return None, rho

        # Binary Search Loop
        # Goal: Find the parameter that is closest to the 'hard' limit while maintaining rho >= 0.
        
        # Calculate maximum iterations needed for requested precision to prevent infinite loops
        safe_step = max(step, 1e-6)
        iterations = int(np.log2((high - low) / safe_step)) + 2
        
        current_low = low
        current_high = high
        
        for _ in range(iterations):
            mid = (current_low + current_high) / 2.0
            
            # Snap to required precision step
            if step > 0:
                mid = round(mid / step) * step
                
            # Boundary safeguard
            mid = max(low, min(high, mid))
            
            params[param_def.name] = mid
            phi = template.generate_formula(**params)
            rho = self.verify(phi)
            
            is_satisfied = (rho >= 0)
            
            if is_satisfied:
                best_valid_param = mid
                best_rho = rho
                
                # If satisfied, try to make the rule tighter (push towards harder side)
                if monotonicity == 'increasing':
                    # Easiest is High. Harder is Low. Try lower bounds.
                    current_high = mid
                else:
                    # Easiest is Low. Harder is High. Try higher bounds.
                    current_low = mid
            else:
                # Violated, need to relax the rule
                if monotonicity == 'increasing':
                    current_low = mid
                else:
                    current_high = mid
            
            # Convergence check
            if abs(current_high - current_low) <= step:
                break
                
        return best_valid_param, best_rho


    def _grid_binary_search(self, template: STLTemplate, primary: ParameterSpace, 
                            secondary: ParameterSpace) -> Tuple[Optional[Dict[str, float]], float]:
        """
        Handles 2-parameter optimization.
        Sweeps the secondary parameter linearly, performing a binary search on the primary
        parameter for each step. Returns the combination with the highest robustness margin.
        """
        best_global_params = None
        best_global_rho = -np.inf
        
        # Sweep secondary parameter
        sec_vals = np.arange(secondary.min_val, secondary.max_val + secondary.step, secondary.step)
        
        for sec_val in sec_vals:
            fixed = {secondary.name: float(sec_val)}
            
            # Binary search primary parameter given the fixed secondary
            prim_val, rho = self._binary_search(template, primary, fixed)
            
            if prim_val is not None and rho > best_global_rho:
                best_global_rho = rho
                best_global_params = {
                    primary.name: prim_val,
                    secondary.name: float(sec_val)
                }
                
        return best_global_params, best_global_rho
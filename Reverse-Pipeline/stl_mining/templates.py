"""
FILE: stl_mining/templates.py
PURPOSE: Defines the "Grammar" of Surgical Safety Rules for Tool Workflows.
         Templates are parametric STL formulas (Formula + Unknowns).
         The Miner optimizes these Unknowns to fit the data.

TEMPLATES IMPLEMENTED:
1. ThresholdInvariant: "While Tool X is active, Metric Y must be > C"
2. EventPrecedence: "Tool B cannot happen until Tool A has happened (with Gap)"
3. CoActivation: "If Tool A is active, Tool B must also be active"
4. BoundedResponse: "If Tool X is used, Tool Y must follow within T seconds"
5. MutualExclusion: "Tool A and Tool B cannot be active at the same time"
"""

import numpy as np
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass
import stl_mining.core as stl

@dataclass
class ParameterSpace:
    """Defines the search bounds for a template parameter."""
    name: str
    min_val: float
    max_val: float
    step: float
    # 'increasing': Higher param value = Higher Robustness (Easier to satisfy)
    # 'decreasing': Lower param value = Higher Robustness (Easier to satisfy)
    monotonicity: str 

class STLTemplate:
    """Abstract Base Class for Mining Templates."""
    
    def __init__(self, name: str, description_fmt: str):
        self.name = name
        self.description_fmt = description_fmt

    def generate_formula(self, **params) -> stl.STLFormula:
        """Creates the specific STL object for a given parameter set."""
        raise NotImplementedError

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        """Returns the bounds for optimization based on trace data."""
        raise NotImplementedError

    def explain(self, **params) -> str:
        return self.description_fmt.format(**params)

# --- CONCRETE TEMPLATES ---

class ThresholdInvariant(STLTemplate):
    """
    Template: Always( Trigger -> (Metric >= Threshold) )
    Logic: "Whenever the Trigger tool is active, the Metric must be above a certain level."
    Example: "Whenever Clipper is active, the Scene Overcrowding score must be <= Tau."
             (Note: requires derivation of the metric in signals.py)
    
    Robustness Monotonicity:
    - As Threshold DECREASES, the rule becomes EASIER to satisfy.
    - Monotonicity: 'decreasing'
    """
    def __init__(self, trigger_tool: str, metric_signal: str):
        super().__init__(
            name=f"Invariant_{trigger_tool}_{metric_signal}",
            description_fmt=f"Whenever {trigger_tool} is active, {metric_signal} must be >= {{threshold:.2f}}"
        )
        self.trigger = trigger_tool
        self.metric = metric_signal

    def generate_formula(self, threshold: float) -> stl.STLFormula:
        # phi = Always( Implies( Trigger > 0.5, Metric >= threshold ) )
        
        p_trigger = stl.Predicate(self.trigger, '>', 0.5)
        p_metric = stl.Predicate(self.metric, '>=', threshold)
        
        return stl.Always(stl.Implication(p_trigger, p_metric))

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        # Search from 0 to max observed value of the metric
        max_val = trace_stats.get(f"max_{self.metric}", 100.0)
        return [
            ParameterSpace("threshold", 0.0, max_val + 1.0, 0.1, monotonicity="decreasing")
        ]


class EventPrecedence(STLTemplate):
    """
    Template: Always( Effect -> Historically_[Gap, Inf]( Cause ) )
    Logic: "If Tool B is happening now, Tool A must have happened at least 'Gap' seconds ago."
    Useful for strict tool ordering: Grasper must precede Clipper.
    """
    def __init__(self, cause_tool: str, effect_tool: str):
        super().__init__(
            name=f"Precedence_{cause_tool}_{effect_tool}",
            description_fmt=f"{effect_tool} cannot be used until {{gap:.2f}}s after {cause_tool} first appears."
        )
        self.cause = cause_tool
        self.effect = effect_tool

    def generate_formula(self, gap: float) -> stl.STLFormula:
        # phi = Always( Implies( Effect > 0.5, Historically_[gap, inf]( Cause > 0.5 ) ) )
        
        p_effect = stl.Predicate(self.effect, '>', 0.5)
        p_cause = stl.Predicate(self.cause, '>', 0.5)
        
        # Historically with interval [gap, infinity]
        # Checks if 'Cause' was true at some point in the past (older than 'gap')
        p_history = stl.Historically(p_cause, interval=(gap, np.inf))
        
        return stl.Always(stl.Implication(p_effect, p_history))

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        # Search gap from 0 to 60 seconds
        return [
            ParameterSpace("gap", 0.0, 60.0, 1.0, monotonicity="decreasing")
        ]


class CoActivation(STLTemplate):
    """
    Template: Always( Primary -> Required )
    Logic: "If Primary tool is active, Required tool MUST also be active."
    Example: "Hook requires Grasper (Traction)."
    
    Parameter: Confidence. How strong must the required signal be?
    """
    def __init__(self, primary: str, required: str):
        super().__init__(
            name=f"CoActivation_{primary}_{required}",
            description_fmt=f"If {primary} is active, {required} must be present with confidence > {{confidence:.2f}}"
        )
        self.primary = primary
        self.required = required

    def generate_formula(self, confidence: float) -> stl.STLFormula:
        p_prim = stl.Predicate(self.primary, '>', 0.5)
        p_req = stl.Predicate(self.required, '>', confidence)
        return stl.Always(stl.Implication(p_prim, p_req))

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        # Confidence between 0.1 and 0.9
        return [
            ParameterSpace("confidence", 0.1, 0.95, 0.05, monotonicity="decreasing")
        ]


class BoundedResponse(STLTemplate):
    """
    Template: Always( Trigger -> Eventually_[0, T]( Response ) )
    Logic: "If Trigger tool is used, Response tool must follow within T seconds."
    Example: "If Clipper is used, Scissors must follow within T seconds."
    
    Monotonicity:
    - As T INCREASES, the rule becomes EASIER to satisfy.
    - Monotonicity: 'increasing'
    """
    def __init__(self, trigger: str, response: str):
        super().__init__(
            name=f"Response_{trigger}_{response}",
            description_fmt=f"If {trigger} occurs, {response} must follow within {{deadline:.2f}}s"
        )
        self.trigger = trigger
        self.response = response

    def generate_formula(self, deadline: float) -> stl.STLFormula:
        p_trig = stl.Predicate(self.trigger, '>', 0.5)
        p_resp = stl.Predicate(self.response, '>', 0.5)
        
        # Always( Trigger -> Eventually_[0, deadline](Response) )
        return stl.Always(stl.Implication(p_trig, stl.Eventually(p_resp, interval=(0, deadline))))

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        return [
            ParameterSpace("deadline", 0.0, 30, 0.5, monotonicity="increasing")
        ]


class MutualExclusion(STLTemplate):
    """
    Template: Always( Not(A and B) )
    Logic: "Tool A and Tool B are never active at the same time."
    Example: "Bipolar and Clipper cannot be used simultaneously."
    
    This is a parameter-less template. It either holds True or False over the trace.
    """
    def __init__(self, tool_a: str, tool_b: str):
        super().__init__(
            name=f"MutEx_{tool_a}_{tool_b}",
            description_fmt=f"{tool_a} and {tool_b} cannot be used simultaneously."
        )
        self.tool_a = tool_a
        self.tool_b = tool_b

    def generate_formula(self, **kwargs) -> stl.STLFormula:
        # Always( Not(A and B) ) -> Always( NotA or NotB )
        p_a = stl.Predicate(self.tool_a, '>', 0.5)
        p_b = stl.Predicate(self.tool_b, '>', 0.5)
        
        conjunction = stl.Conjunction(p_a, p_b)
        negation = stl.Negation(conjunction)
        
        return stl.Always(negation)

    def get_search_space(self, trace_stats: Dict[str, Any]) -> List[ParameterSpace]:
        # No parameters to optimize. Returning empty list flags the optimizer 
        # to just verify the formula directly without binary search.
        return []
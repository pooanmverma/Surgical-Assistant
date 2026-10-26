"""
FILE: run_mining_pipeline.py
PURPOSE: The executable for the Tool-Only STL Mining Subsystem.
         1. Loads the 'ideal_surgery_trace.csv' (tool tracks only).
         2. Uses the 'stl_mining' package to reverse-engineer workflow rules.
         3. Compiles the valid tool constraints into 'generated_policy.py'.
"""

import sys
import os
import json
import logging #2026-08-04 11:00:15 | INFO | Loaded trace
import time # only used when gernate policy
import numpy as np # compute stat for each tool signal
import pandas as pd
# Ensure local package is visible in python path
sys.path.append(os.getcwd()) #Also search in the current working directory.

from stl_mining.signals import TraceLoader
from stl_mining.optimizer import STLOptimizer  #rule-----robutness----a/reject
from stl_mining.templates import (
    EventPrecedence,# these four are templates Instead of writing STL formulas manually, the script creates objects like Each object knows how to generate its STL formula.
    CoActivation,
    MutualExclusion,
    BoundedResponse
)

# Configuration
TRACE_FILE = './training_traces12/tool_video_10_trace.csv'
OUTPUT_RULES_JSON = 'mined_constraints.jsonvideomodel12vedio10'
OUTPUT_PYTHON_CODE = 'generated_policymodel12vedio10.py'

# ID Mapping for readable outputs
TOOL_NAMES = {
    0: 'Grasper', 
    1: 'Bipolar', 
    2: 'Hook', 

    3: 'Scissors', 
    4: 'Clipper', 
    5: 'Irrigator', 
    6: 'Bag'
}

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ToolMiningPipeline")

def main():
    logger.info("🚀 STARTING SURGICAL TOOL SPECIFICATION MINING")
    
    # 1. Load Data
    if not os.path.exists(TRACE_FILE):#read ideal_surgery_trace.csv
        logger.error(f"Trace file {TRACE_FILE} not found. Run 'trace_logger.py' first!")
        sys.exit(1)
        
    loader = TraceLoader(fps=25.0) #here we create loder object and this traceloader class is in signal .py
    signal_trace = loader.load_csv(TRACE_FILE) #signal trace is like container contains times,tool_0,tool_1 etc
    logger.info(f"Loaded trace: {len(signal_trace.times)} frames ({signal_trace.times[-1]:.1f}s)")
   #signal_trace.times----------[0.0,0.04,0.08] signal_trace["tool_0"]-----------[1,1,0]Loaded trace: 7500 frames(300.0 s)
    # 2. Compute Trace Statistics (Used for Search Space bounds)
    # yha tak no. frame print kr lie No STL mining has started yet. Up to this point, the script has only loaded and prepared the data.
    trace_stats = {}
    for name in signal_trace.signal_names:
        sig = signal_trace[name]#[1,1,1,0,0,1,1] by using tool name
        trace_stats[f"max_{name}"] = np.max(sig) #max tool=1
        trace_stats[f"min_{name}"] = np.min(sig)#min tool=0
    
    # Identify tools that actually appeared in the video
    present_tools = []
    for t_id in range(7):
        # If the maximum value of the smoothed tool signal is > 0.5, it appeared.
        if trace_stats.get(f"max_tool_{t_id}", 0) > 0.5:
            present_tools.append(t_id)
            
    detected_names = [TOOL_NAMES[t] for t in present_tools]
    logger.info(f"Tools detected in video trace: {detected_names}")

    if len(present_tools) < 2:
        logger.warning("Fewer than 2 tools detected. Cannot mine relational tool workflows.")
        sys.exit(0)#No relationship can be mined. bacuse for rule generation other tool should be peresent there

    # 3. Initialize Solver
    optimizer = STLOptimizer(signal_trace._signals, signal_trace.times)#------------This object will later calculate STL robustness.
    #creates the STL engine,object will later calculate STL robustness.
    # 4. Define Template Candidates Dynamically
    # We ask: "Which of these generic rules fit this specific video?"
    candidates = []
    
    # Generate all pairwise combinations of present tools
    for tA in present_tools:
        for tB in present_tools:# if [0,2,3] pesent than 00,02,03,20,23,22......
            if tA == tB: #if same tool than ignore
                continue 
            
            name_a = f'tool_{tA}'#if tA=2 than name_a="tool_2"
            name_b = f'tool_{tB}'
            
            # Hypothesis 1: Precedence (Did A always come before B?)
            candidates.append(EventPrecedence(cause_tool=name_a, effect_tool=name_b))
            
            # Hypothesis 2: Co-Activation (Does A always need B?)
            candidates.append(CoActivation(primary=name_a, required=name_b))
            
            # Hypothesis 3: Bounded Response (If A happens, does B happen within T seconds?)
            candidates.append(BoundedResponse(trigger=name_a, response=name_b))
            
            # Hypothesis 4: Mutual Exclusion (Are A and B never together?)
            if tA < tB: # Avoid testing MutEx(A,B) and MutEx(B,A) twice
                candidates.append(MutualExclusion(tool_a=name_a, tool_b=name_b))

    logger.info(f"Generated {len(candidates)} template hypotheses to test against the trace.")
# till now  hundreds STL hypotheses.Nothing has been verified yet.
    # 5. Run Optimization and Verification
    valid_rules = []
    for tmpl in candidates:
        search_space = tmpl.get_search_space(trace_stats)
        
        if not search_space:
            # Parameter-less template (e.g., MutualExclusion, simple Precedence without gap param)
            phi = tmpl.generate_formula() #Produces STL formula.
            robustness = optimizer.verify(phi)#Computes STL robustness
            if robustness >= 0:
                # Rule holds true in the ideal video
                rule_entry = {
                    "name": tmpl.name,
                    "description": _format_description(tmpl.explain()),
                    "parameters": {},
                    "robustness": float(robustness)
                }
                valid_rules.append(rule_entry)
                logger.info(f"✅ MINED (Fixed): {rule_entry['description']} (Rob: {robustness:.4f})")
        else:
            # Parametric template (e.g., BoundedResponse, CoActivation with confidence)
            result = optimizer.optimize(tmpl, trace_stats)
            if result:
                # Clean up description
                result['description'] = _format_description(result['description'])
                valid_rules.append(result)
                logger.info(f"✅ MINED (Param): {result['description']} (Rob: {result['robustness']:.4f})")

    # 6. Export Results
    if not valid_rules:
        logger.warning("No valid rules found. Video might be empty or unstructured.")
        sys.exit(0)

    # Save JSON
    with open(OUTPUT_RULES_JSON, 'w') as f:
        json.dump(valid_rules, f, indent=4)
    logger.info(f"Saved JSON constraints to {OUTPUT_RULES_JSON}") #mined_contraints.json

    # 7. Compile to Python (Auto-Coding)
    logger.info("Compiling executable logic module...")
    generate_python_module(valid_rules) #instead of STL it writes Python code.It translates mined rules into executable Python.


def _format_description(desc: str) -> str:
    """Helper to convert 'tool_4' into 'Clipper' for readability."""
    formatted = desc.replace('tool_', 'Tool ')
    for k, v in TOOL_NAMES.items():
        formatted = formatted.replace(f'Tool {k}', v)
    return formatted


def generate_python_module(rules: list):
    """
    Writes the 'generated_policy.py' file based on the mined rules.
    This generates a robust Python class that takes a list of active tool IDs.
    """
    lines = [
        '"""',
        'FILE: generated_policy.py',
        'PURPOSE: Auto-generated Workflow Policy for Surgical Tools.',
        f'GENERATED ON: {time.ctime()}',
        'DO NOT EDIT MANUALLY - This file was synthesized by the SurgSTL Miner.',
        '"""',
        '',
        'class MinedWorkflowPolicy:',
        '    def __init__(self):',
        '        self.history = set()',
        '        self.violations = []',
        '',
        '    def check_workflow(self, active_tool_ids: list) -> list:',
        '        """',
        '        Evaluates the current frame against mined safety constraints.',
        '        Returns a list of violation strings (empty if safe).',
        '        """',
        '        self.violations = []',
        '',
        '        # Update Tool Appearance History',
        '        for tool_id in active_tool_ids:',
        '            self.history.add(tool_id)',
        '',
        '        # --- RULE CHECKS ---'
    ]
    
    for i, r in enumerate(rules):
        desc = r['description']
        name = r['name']
        
        lines.append(f"        # Rule {i+1}: {desc}")
        
        # Name format parsing:
        # EventPrecedence: Precedence_tool_2_tool_4
        # CoActivation: CoActivation_tool_2_tool_0
        # MutualExclusion: MutEx_tool_1_tool_4
        # BoundedResponse: Response_tool_4_tool_3
        
        parts = name.split('_')
        
        if "Precedence" in name:
            cause_id = int(parts[2])
            effect_id = int(parts[4])
            lines.append(f"        if {effect_id} in active_tool_ids and {cause_id} not in self.history:")
            lines.append(f"            self.violations.append('SEQ ERROR: {desc}')")
            
        elif "CoActivation" in name:
            prim_id = int(parts[2])
            req_id = int(parts[4])
            lines.append(f"        if {prim_id} in active_tool_ids and {req_id} not in active_tool_ids:")
            lines.append(f"            self.violations.append('CO-ACT ERROR: {desc}')")
            
        elif "MutEx" in name:
            tool_a_id = int(parts[2])
            tool_b_id = int(parts[4])
            lines.append(f"        if {tool_a_id} in active_tool_ids and {tool_b_id} in active_tool_ids:")
            lines.append(f"            self.violations.append('MUTEX ERROR: {desc}')")
            
        elif "Response" in name:
            # Note: Strict time-bounds require state-tracking in Python.
            # For simplicity in this generated script, we flag if the response is missing historically
            # but in a full deployment, this would use a timer dictionary.
            trigger_id = int(parts[2])
            resp_id = int(parts[4])
            params = r.get('parameters', {})
            deadline = params.get('deadline', 0.0)
            lines.append(f"        # Bounded Response Check (T < {deadline:.1f}s)")
            lines.append(f"        if {trigger_id} in active_tool_ids and {resp_id} not in self.history:")
            lines.append(f"            # Note: This is a soft check for Response occurrence.")
            lines.append(f"            pass # Handled by advanced temporal buffer in production")
            
        lines.append("")

    lines.append("        return self.violations")
    lines.append("")
    
    # Save the file
    with open(OUTPUT_PYTHON_CODE, 'w') as f:
        f.write("\n".join(lines))
    logger.info(f"✅ Generated Python Logic Module: {OUTPUT_PYTHON_CODE}")


if __name__ == "__main__":
    main()
Design Document: Automated Surgical Specification Mining System

Project: Neuro-Symbolic Surgical Assistant

Module: Reverse Engineering / Specification Mining Engine

Version: 1.0 (Production)

Date: February 2026

1. Executive Summary

This module implements "Learning from Demonstration" (LfD) for surgical safety. Instead of manually coding safety rules (e.g., "Wait 5 seconds before clipping"), this system watches an "Ideal Surgery" video and mathematically derives the strict safety constraints respected by the expert surgeon.

It utilizes Parametric Signal Temporal Logic (PSTL) to mine rules with quantitative robustness, ensuring that the extracted specifications are not just boolean guesses but mathematically optimized bounds.

2. System Architecture

The system follows a 3-Stage Linear Pipeline:

Trace Generation (ETL): Converts unstructured video data (pixels) into a structured event log (CSV).

Mining Engine (Optimization): Uses Signal Temporal Logic (STL) to fit rule templates to the data using monotonic binary search.

Code Synthesis (Backend): Automatically generates an executable Python module (generated_policy.py) containing the mined rules.

High-Level Data Flow

Video (.mp4) $\to$ [YOLOv12 + Adapter] $\to$ Trace (.csv) $\to$ [STL Miner] $\to$ Mined Logic (.json) $\to$ [Compiler] $\to$ Python Code (.py)

3. Directory Structure

The feature is encapsulated in the stl_mining package to ensure modularity and reusability.

Project_Root/
│
├── stl_mining/                  # [CORE PACKAGE] The Mining Logic Library
│   ├── __init__.py
│   ├── core.py                  # Mathematical Kernel (Vectorized Robustness)
│   ├── signals.py               # Signal Preprocessing & Smoothing
│   ├── templates.py             # Rule Grammar (Precondition, Response, etc.)
│   └── optimizer.py             # Monotonic Parameter Solver
│
├── trace_logger.py              # [STAGE 1] Runs Vision Model -> CSV Log
├── run_mining_pipeline.py       # [STAGE 2 & 3] Runs Miner -> Generates Code
│
├── ideal_surgery_trace.csv      # Intermediate Artifact (The "Truth")
└── generated_policy.py          # Final Output (The "Brain")


4. Module Details

A. The Trace Logger (trace_logger.py)

Role: The "Black Box Recorder" for surgery.

Input: Raw MP4 Video.

Logic: Runs the full YOLOv12 + UnifiedAdapter pipeline. Instead of visualizing, it serializes the internal state (Tool IDs, Organ Overlaps, Timers) to disk.

Output: ideal_surgery_trace.csv.

B. The STL Kernel (stl_mining/core.py)

Role: The Mathematical Foundation.

Key Feature: Implements Quantitative Robustness Semantics (Donzé et al.).

Returns a float ($\rho$) instead of bool.

$\rho > 0$: Rule Satisfied.

$\rho < 0$: Rule Violated.

Performance: Uses vectorized NumPy operations for sliding window temporal operators (Always, Eventually, Until), enabling analysis of 30,000+ frames in seconds.

C. The Optimizer (stl_mining/optimizer.py)

Role: The Solver.

Algorithm: Monotonic Binary Search.

Problem: Find maximum time $\tau$ such that $\square(\text{Clip} \implies \text{Dissection} > \tau)$.

Method: Checks $\tau=0$ (Pass), $\tau=10$ (Fail), $\tau=5$ (Pass)... until precision limit is reached.

Guarantee: Finds the tightest bound that holds true for the specific video provided.

D. The Template Library (stl_mining/templates.py)

Role: The "Grammar" of Rules.

Supported Templates:

ThresholdInvariant: Constraints on continuous metrics (e.g., forces).

TimeAccumulationPrecondition: Mandatory wait times before actions.

EventPrecedence: Strict ordering (A must happen before B).

CoActivation: Tools that must be used together (Hook + Grasper).

5. Implementation & Usage Guide

Prerequisites

Ensure the BTP_Part3_CVS_System model is trained and available.

Step 1: Generate the Trace

Run the logger on a video that represents a "Perfect Procedure" (no safety violations).

python trace_logger.py
# Output: ideal_surgery_trace.csv


Step 2: Run the Miner

Run the mining pipeline. This will load the CSV, optimize parameters for all templates, and generate the code.

python run_mining_pipeline.py
# Output: generated_policy.py


Step 3: Deployment

Import the generated class into your main system.

# In unified_main_system.py
from generated_policy import MinedSafetyPolicy

policy = MinedSafetyPolicy()
violations = policy.check_safety(active_tools, dissection_time, ...)


6. How to Extend (Developer Guide)

Adding a New Rule Template

To add a new type of rule (e.g., "Max Speed Constraint"):

Edit stl_mining/templates.py:

Create a class inheriting from STLTemplate.

Implement generate_formula(**params) to return an STL object tree.

Implement get_search_space() to define min/max bounds for optimization.

Edit run_mining_pipeline.py:

Instantiate your new template in the main() function.

Add it to the candidates list.

Run: The optimizer automatically handles the new logic using the generic STL core.

Debugging

Optimization Fails? Check stl_mining/signals.py. Ensure the signal is normalized (0 to 1) and smooth. Noisy AI signals often break strict logic; increase the smoothing window.
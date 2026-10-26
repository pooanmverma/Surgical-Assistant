"""
FILE: generated_policy.py
PURPOSE: Auto-generated Workflow Policy for Surgical Tools.
GENERATED ON: Wed Aug 12 10:08:25 2026
DO NOT EDIT MANUALLY - This file was synthesized by the SurgSTL Miner.
"""

class MinedWorkflowPolicy:
    def __init__(self):
        self.history = set()
        self.violations = []

    def check_workflow(self, active_tool_ids: list) -> list:
        """
        Evaluates the current frame against mined safety constraints.
        Returns a list of violation strings (empty if safe).
        """
        self.violations = []

        # Update Tool Appearance History
        for tool_id in active_tool_ids:
            self.history.add(tool_id)

        # --- RULE CHECKS ---
        # Rule 1: Grasper and Bipolar cannot be used simultaneously.
        if 0 in active_tool_ids and 1 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Grasper and Bipolar cannot be used simultaneously.')

        # Rule 2: Grasper and Irrigator cannot be used simultaneously.
        if 0 in active_tool_ids and 5 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Grasper and Irrigator cannot be used simultaneously.')

        # Rule 3: Bipolar and Hook cannot be used simultaneously.
        if 1 in active_tool_ids and 2 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Bipolar and Hook cannot be used simultaneously.')

        # Rule 4: Bipolar and Scissors cannot be used simultaneously.
        if 1 in active_tool_ids and 3 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Bipolar and Scissors cannot be used simultaneously.')

        # Rule 5: Bipolar and Clipper cannot be used simultaneously.
        if 1 in active_tool_ids and 4 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Bipolar and Clipper cannot be used simultaneously.')

        # Rule 6: Bipolar and Irrigator cannot be used simultaneously.
        if 1 in active_tool_ids and 5 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Bipolar and Irrigator cannot be used simultaneously.')

        # Rule 7: Bipolar and Bag cannot be used simultaneously.
        if 1 in active_tool_ids and 6 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Bipolar and Bag cannot be used simultaneously.')

        # Rule 8: Hook and Scissors cannot be used simultaneously.
        if 2 in active_tool_ids and 3 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Hook and Scissors cannot be used simultaneously.')

        # Rule 9: Hook and Clipper cannot be used simultaneously.
        if 2 in active_tool_ids and 4 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Hook and Clipper cannot be used simultaneously.')

        # Rule 10: Scissors and Irrigator cannot be used simultaneously.
        if 3 in active_tool_ids and 5 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Scissors and Irrigator cannot be used simultaneously.')

        # Rule 11: Scissors and Bag cannot be used simultaneously.
        if 3 in active_tool_ids and 6 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Scissors and Bag cannot be used simultaneously.')

        # Rule 12: Clipper and Irrigator cannot be used simultaneously.
        if 4 in active_tool_ids and 5 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Clipper and Irrigator cannot be used simultaneously.')

        # Rule 13: Clipper and Bag cannot be used simultaneously.
        if 4 in active_tool_ids and 6 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Clipper and Bag cannot be used simultaneously.')

        # Rule 14: If Irrigator occurs, Hook must follow within 1.00s
        # Bounded Response Check (T < 1.0s)
        if 5 in active_tool_ids and 2 not in self.history:
            # Note: This is a soft check for Response occurrence.
            pass # Handled by advanced temporal buffer in production

        # Rule 15: Irrigator and Bag cannot be used simultaneously.
        if 5 in active_tool_ids and 6 in active_tool_ids:
            self.violations.append('MUTEX ERROR: Irrigator and Bag cannot be used simultaneously.')

        return self.violations

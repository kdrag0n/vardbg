import abc
import copy
import sys
from typing import TYPE_CHECKING

import dictdiffer

from . import data, internal

if TYPE_CHECKING:
    from .debugger import Debugger


ALLOWED_EVENTS = {"call", "line", "return"}
DISALLOWED_FUNC_NAMES = {"<genexpr>", "<listcomp>", "<dictcomp>"}


class FrameScope:
    """Scope of one stack frame and its snapshots."""

    def __init__(self):
        # Previous frame and its locals
        self.prev_frame_info = None
        self.prev_event = None
        self.prev_locals = {}
        # New frame's locals
        self.new_locals = {}


class Tracer(abc.ABC):
    def __init__(self: "Debugger"):
        # Function being debugged
        self.func = None

        # Stack with frame scopes
        self.scope_stack = []

        # Propagate initialization to other mixins
        super().__init__()

    def trace_callback(self: "Debugger", frame, event, arg):
        """Frame execution callback"""

        # Ignore irrelevant events, but still attach to the next one
        if event not in ALLOWED_EVENTS:
            return self.trace_callback

        # Ignore our internal functions
        code = frame.f_code
        if code in internal.INTERNAL_FUNC_CODES:
            return

        # Ignore comprehensions and generator expressions
        # (they act strangely and most people wouldn't consider them to be functions)
        if code.co_name in DISALLOWED_FUNC_NAMES:
            return

        # Obtain frame scope
        if event == "call":
            # Create and push a new scope for this stack frame (used for all of its snapshots)
            scope = FrameScope()
            self.scope_stack.append(scope)
        else:
            # Don't use pop since we may need to reuse the scope
            scope = self.scope_stack[-1]

        # The first frame is when function arguments are populated, so it's important
        # Set itself to the previous frame since its line number *is* where function arguments are defined
        frame_info = data.FrameInfo(frame, relative=self.use_relative_paths)
        if scope.prev_frame_info is None:
            scope.prev_frame_info = frame_info
            scope.prev_event = event

        # Only invoke profiler if the last event was a normal line, since calls haven't executed anything yet
        # and returns aren't actually relevant to the code
        should_profile = scope.prev_event == "line"
        if should_profile:
            # Call profiler first to avoid counting the time it takes to copy locals
            self.profile_complete_frame(scope.prev_frame_info)

        # Get new locals and copy them so they don't change on the next frame
        scope.new_locals = copy.deepcopy(frame.f_locals)

        # Render output prefix for this frame
        self.out.write_cur_frame(scope.prev_frame_info)

        # Skip profiler for the first frame since it's before any real execution (just the function call)
        if should_profile:
            # Call profiler to print the last frame's execution
            self.profile_print_frame(scope.prev_frame_info)

        # Diff and print changes
        diff = dictdiffer.diff(scope.prev_locals, scope.new_locals)
        self.process_locals_diff(diff, scope.prev_frame_info, scope.new_locals)

        if event == "return":
            # Use pop() to return to the previous frame since the scope is no longer needed
            self.scope_stack.pop()
        else:
            # Update previous frame info in preparation for the next frame
            scope.prev_frame_info = frame_info
            scope.prev_event = event
            scope.prev_locals = scope.new_locals

        # Don't profile returns (performance isn't user code) or calls (nothing's actually executed yet)
        if event == "line":
            self.profile_start_frame()

        # Subscribe to the next frame, if any
        return self.trace_callback

    def run(self: "Debugger", func, *args, **kwargs):
        self.func = func

        # Run function with trace callback registered
        sys.settrace(self.trace_callback)
        self.profile_start_exec()
        ret = self.func(*args, **kwargs)
        self.profile_end_exec()
        sys.settrace(None)

        self.out.write_summary(self.vars, self.exec_start_time, self.exec_stop_time, self.frame_exec_times)
        return ret

    internal.add_funcs(run)

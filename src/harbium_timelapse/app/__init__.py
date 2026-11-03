"""App services: what a GUI needs that a single command doesn't.

Background jobs that can be cancelled, project state that survives closing the
app, recent projects, and the operations the screens call. No GUI code lives
here: everything is plain Python, tested without a window, and the command
line can use it too.
"""

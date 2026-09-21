# Repository conventions

Use Python 3.11+ standard library. Shell entrypoints are POSIX sh.
Keep model configuration separate from tool adapters and collaboration rules.
Native and tmux modes must not mix. Never add implicit permission bypasses.
Store user configuration and runtime state in XDG directories.
Run `python3 -B -m unittest discover -s tests -v`; test tmux using isolated
servers and fake coding CLIs, never the user's active panes or paid model calls.
Validate shell wrappers with `/bin/sh -n` and ShellCheck.

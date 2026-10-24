# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The scheduled tasks of VIPER-AI.

``definitions`` holds the seven tasks (the truth, with the changes of the installation in ``<root>/scheduler/tasks.json``); ``manager`` deploys, edits,
deletes and runs them; ``windows`` (Task Scheduler) and ``linux`` (systemd user timers) are the deployment backends; ``status`` is what a backend reports.
There is no scheduler process of VIPER: the operating system runs the tasks.
"""

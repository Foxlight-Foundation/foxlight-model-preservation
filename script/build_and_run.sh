#!/bin/zsh
set -euo pipefail
# Explicit new outputs keep prior acceptance artifacts and running owners intact.
task_repo_dir="${0:A:h:h}"
task_app_output=""
task_next_output=0
for task_build_arg in "$@"; do
  if [[ "$task_next_output" == 1 ]]; then task_app_output="$task_build_arg"; task_next_output=0; fi
  if [[ "$task_build_arg" == --output ]]; then task_next_output=1; fi
done
if [[ -z "$task_app_output" ]]; then
  print -u2 'Pass the explicit package_desktop.py build inputs, including a new --output .app.'
  exit 2
fi
python3 "$task_repo_dir/scripts/package_desktop.py" "$@"
/usr/bin/open -n "$task_app_output"

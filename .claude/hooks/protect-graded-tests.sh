#!/usr/bin/env bash
# Refuse edits to the test files that GRADE a lab.
# PreToolUse on Write|Edit.
#
# Why: the fastest way to make a failing lab pass is to change the test. Every
# training room discovers this. The lab is the code, not the assertion - so the
# graded tests are protected the way a real repository protects generated files
# and vendored dependencies.
#
# You may still write your OWN tests anywhere: only these exact files are shut.
# To override deliberately (you are the trainer, or you found a real bug in a
# test):  LAB_ALLOW_TEST_EDITS=1
set -uo pipefail
[ "${LAB_ALLOW_TEST_EDITS:-0}" = "1" ] && exit 0

path=$(cat | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))
except Exception: pass') || exit 0
[ -n "$path" ] || exit 0

case "$path" in
  */day1-foundations/lab1-bare-metal-loop/*/test_agent.py \
  |*/day1-foundations/lab1-bare-metal-loop/*/test-agent.mjs \
  |*/day1-foundations/lab3-failure-gallery/test_gallery.py \
  |*/day1-foundations/lab2-tool-schema-clinic/score.py \
  |*/day1-foundations/lab2-tool-schema-clinic/tasks.jsonl)
      echo "Blocked: ${path##*/} is what grades this lab." >&2
      echo "Change the code under test, not the test. Write your own tests in" >&2
      echo "any other file. Deliberate override: LAB_ALLOW_TEST_EDITS=1" >&2
      exit 2 ;;
esac
exit 0

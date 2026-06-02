#!/usr/bin/env python3
"""UserPromptSubmit hook: summon LUTZ when the prompt opens with 'hello/hey lutz'.

The harness feeds the prompt as JSON on stdin ({"prompt": "..."}). Plain stdout
is injected into the session as context, so when the greeting matches we print
the instruction to load the lutz-partner skill; otherwise we print nothing.
"""

import json
import re
import sys

prompt = json.load(sys.stdin).get("prompt", "")

if re.match(r"\s*(hello|hey)\s+lutz\b", prompt, re.IGNORECASE):
    print(
        "The user is summoning LUTZ. Invoke the lutz-partner skill now "
        "(read persona.md and the strategy docs first), then respond in character."
    )

import json, os
from pathlib import Path
Path(os.environ["WF_OUTPUTS"]).write_text(json.dumps({"text": "hello"}))

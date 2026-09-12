import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "send-voicemail-signal.sh"


@unittest.skipUnless(
    os.name == "posix" and shutil.which("sh") and shutil.which("jq") and shutil.which("base64"),
    "Signal AGI integration test requires POSIX sh, jq, and base64",
)
class SignalDeliveryTests(unittest.TestCase):
    def run_delivery(self, http_code: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bin_directory = root / "bin"
        bin_directory.mkdir()
        capture_file = root / "request.json"
        recording = root / "message.wav"
        recording.write_bytes(b"RIFF-test-audio")

        mock_curl = bin_directory / "curl"
        mock_curl.write_text(
            """#!/bin/sh
output_file=
while [ \"$#\" -gt 0 ]; do
  if [ \"$1\" = \"--output\" ]; then
    shift
    output_file=\"$1\"
  fi
  shift
done
cat > \"$CAPTURE_FILE\"
printf '{\"accepted\":true}' > \"$output_file\"
printf '%s' \"$MOCK_HTTP_CODE\"
""",
            encoding="utf-8",
        )
        mock_curl.chmod(0o755)

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{bin_directory}{os.pathsep}{environment['PATH']}",
                "CAPTURE_FILE": str(capture_file),
                "MOCK_HTTP_CODE": http_code,
                "SIGNAL_API_URL": "http://signal.invalid/v2/send",
                "SIGNAL_NUMBER": "+61000000000",
                "SIGNAL_RECIPIENT": "+61000000001",
            }
        )
        result = subprocess.run(
            ["sh", str(SCRIPT), str(recording), "61400000000"],
            input="\n",
            text=True,
            capture_output=True,
            env=environment,
            check=True,
        )
        return result, recording, json.loads(capture_file.read_text(encoding="utf-8"))

    def test_success_deletes_the_delivered_recording(self):
        result, recording, request = self.run_delivery("202")
        self.assertFalse(recording.exists())
        self.assertIn('SET VARIABLE SIGNAL_SENT "1"', result.stdout)
        self.assertEqual(request["recipients"], ["+61000000001"])
        self.assertTrue(request["base64_attachments"][0].startswith("data:audio/wav;"))

    def test_failure_retains_the_recording(self):
        result, recording, _ = self.run_delivery("503")
        self.assertTrue(recording.exists())
        self.assertIn('SET VARIABLE SIGNAL_SENT "0"', result.stdout)
        self.assertIn("recording retained", result.stdout)


if __name__ == "__main__":
    unittest.main()


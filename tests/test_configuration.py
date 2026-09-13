from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SpruikConfigurationTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_signal_is_primary_and_sip_is_the_fallback(self):
        dialplan = self.read("config/extensions.conf.template")
        self.assertIn("PJSIP_DIAL_CONTACTS(101)", dialplan)
        self.assertIn("PJSIP_DIAL_CONTACTS(102)", dialplan)
        standard = dialplan.index("exten => standard,1")
        sip = dialplan.index("[ring-endpoints]")
        signal = dialplan.index("[signal-route]")
        self.assertIn("Goto(signal-route,s,1)", dialplan[standard:sip])
        self.assertIn("?ring-endpoints,s,1", dialplan[signal:])
        self.assertIn("AudioSocket(${SIGNAL_AUDIO_UUID}", dialplan)
        self.assertIn("?voicemail,s,1", dialplan)

    def test_unsuccessful_sip_and_signal_attempts_fall_back_to_voicemail(self):
        dialplan = self.read("config/extensions.conf.template")
        self.assertIn(
            "Dial(PJSIP/101&PJSIP/102,${RING_SECONDS},tm(spruik-promo))",
            dialplan,
        )
        self.assertIn('${DIALSTATUS}" != "ANSWER', dialplan)
        self.assertIn("Goto(signal-route,s,1)", dialplan)
        self.assertIn('${SIGNAL_RESULT}" != "connected', dialplan)

    def test_call_history_never_passes_caller_identity(self):
        dialplan = self.read("config/extensions.conf.template")
        event_lines = [
            line for line in dialplan.splitlines() if "log-spruik-event.py" in line
        ]
        self.assertGreaterEqual(len(event_lines), 3)
        self.assertTrue(
            all(
                "CALLERID" not in line and "NORMALIZED_CALLER" not in line
                for line in event_lines
            )
        )

    def test_voicemail_does_not_depend_on_a_stock_beep_file(self):
        dialplan = self.read("config/extensions.conf.template")
        self.assertIn("PlayTones(1000/500)", dialplan)
        self.assertIn("Record(${VOICEMAIL_FILE},5,120,kq)", dialplan)
        self.assertNotIn("Playback(beep)", dialplan)

    def test_internal_preview_extensions_are_present(self):
        dialplan = self.read("config/extensions.conf.template")
        for extension in ("600", "601", "602"):
            self.assertIn(f"exten => {extension},1", dialplan)

    def test_recordings_are_persistent_in_both_runtimes(self):
        compose = self.read("compose.yaml")
        kubernetes = self.read("deploy/kubernetes/deployment.yaml")
        self.assertIn("pabx-voicemail:/var/spool/asterisk/voicemail", compose)
        self.assertIn("claimName: spruik-voicemail", kubernetes)

    def test_signal_identity_is_not_in_the_kubernetes_configmap(self):
        configmap = self.read("deploy/kubernetes/configmap.yaml")
        secret_example = self.read("deploy/kubernetes/secret.example.yaml")
        self.assertNotIn("SIGNAL_NUMBER:", configmap)
        self.assertIn("SIGNAL_NUMBER:", secret_example)

    def test_management_token_is_secret_and_ui_data_is_persistent(self):
        configmap = self.read("deploy/kubernetes/configmap.yaml")
        secret_example = self.read("deploy/kubernetes/secret.example.yaml")
        deployment = self.read("deploy/kubernetes/deployment.yaml")
        self.assertNotIn("SPRUIK_ADMIN_TOKEN:", configmap)
        self.assertIn("SPRUIK_ADMIN_TOKEN:", secret_example)
        self.assertIn("claimName: spruik-data", deployment)

    def test_backup_is_encrypted_and_refuses_overwrite(self):
        backup = self.read("scripts/backup-spruik.sh")
        self.assertIn("age -r", backup)
        self.assertIn('if [ -e "$output" ]', backup)
        self.assertNotIn('tar -czf "$output"', backup)

    def test_old_project_identity_is_gone(self):
        checked_files = [
            "Dockerfile",
            "README.md",
            "scripts/entrypoint.sh",
            "config/pjsip.conf.template",
            "deploy/kubernetes/deployment.yaml",
        ]
        for relative_path in checked_files:
            content = self.read(relative_path).lower()
            self.assertNotIn("tertius-pabx", content)
            self.assertNotIn("/opt/tertius", content)


if __name__ == "__main__":
    unittest.main()

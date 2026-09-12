# Spruik roadmap

## Portable PBX core

- [x] One SIP trunk registration shared by two endpoints.
- [x] Simultaneous endpoint ringing with answer, decline, busy, timeout, and unavailable handling.
- [x] Kokoro-generated standard, personalised, promotional hold, voicemail, and confirmation audio.
- [x] Persistent voicemail recordings with optional Signal delivery.
- [x] Docker Compose and Kubernetes configurations from the same image.
- [x] Regression tests for call routing, the generated voicemail tone, persistence, and Signal delivery.
- [ ] Retry retained Signal messages with bounded backoff and delivery history.
- [ ] Publish signed, versioned container releases and a reproducible upgrade procedure.

## Management experience

- [x] Show trunk registration, endpoint reachability, and active channel count.
- [ ] Show recent call outcomes without exposing raw caller data in logs or metrics.
- [x] Edit and preview standard, promotional, voicemail, and confirmation greetings.
- [ ] Manage caller profiles without storing personal contacts in Git.
- [x] Browse, play, download, and delete retained voicemails.
- [ ] Retry retained voicemails from the UI with delivery history.
- [x] Protect management access and keep credentials out of browser storage and logs.
- [x] Trigger internal welcome, hold, and voicemail test calls.

## Mobile endpoint

- [ ] Select an iPhone endpoint that reliably wakes on the lock screen.
- [ ] Verify two-way audio, caller identity, simultaneous ringing, decline behaviour, and call waiting.
- [ ] Add private remote access without exposing SIP or RTP to the public internet.
- [ ] Keep the mobile transport behind an interface so SIP, Mumble, or another audio endpoint can be replaced.

## Later enhancements

- [ ] Voicemail transcription and searchable summaries.
- [ ] Spam screening and caller intent collection.
- [ ] Optional recording with an explicit consent policy.
- [ ] Monitoring, alerts, retention rules, and call-quality metrics.

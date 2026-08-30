# DCS-2230 PTT Tool

A Python tool (GUI) for the D-Link DCS-2230 IP camera that enables Push-to-Talk (PTT) while streaming Audio/Video in parallel via VLC.

## Disclaimer

This project heavily utilizes AI (Claude). I did browse over the code and checked for anything obviously malicious, but I'm not a professional software developer. Review the code yourself before running it if that matters to you.
Is this project pretty? No. Does it do its job? Yes, completely. That's all that matters to me.

## Backstory

I needed a camera to monitor a measuring room from a control room via LAN, so I bought a used D-Link DCS-2230 for little money.
However, PTT only works via a the camera's webpage and via Internet Explorer (ActiveX, lol). RTSP streaming works fine for A/V. So I needed an external tool for this.


## Prerequisites

- **Python 3** and `pip`
- **[ffmpeg](https://ffmpeg.org/download.html)** installed and available on your system `PATH`. It is required for PTT audio encoding. Test with `ffmpeg -version` in a terminal.

## How to use

1. Install Python and pip.
2. Download this repo.
3. Open a command line inside the downloaded folder.
4. Install required packages:
   ```
   pip install -r requirements.txt
   ```
5. Run the tool:
   ```
   python main.py
   ```
6. Before doing anything else, go to **Settings** → set the IP address of your camera and check username/password. Set a PTT hotkey if desired.
7. In the main window, click **Connect**.
8. You should now be able to adjust settings (e.g. white balance).
9. Press the PTT key to talk. Audio has a delay of about a second. This is likely due to the codec and minimum packet size. If you know a fix, I'm open to suggestions.

RTSP video streams fine via VLC, e.g.:
```
rtsp://192.168.106.20:554//live1.sdp
```
Set `:network-caching=10` (or similar) in VLC. 10 ms was stable in my testing via LAN: The Stream stabilizes after a few seconds of instability.

## What I learned

- While you can select the audio codec for the camera-to-host audio stream, **PTT audio is always G.726**, regardless of what's selected in the D-Link web interface. The setting only affects the listen direction, not talk.
- The camera uses ePTZ; it has **no mechanical zoom**. Zoom only works through the web interface (tested in Microsoft Edge via IE mode).
- ePTZ (moving the camera frame) doesn't seem to change the streamed video either. I sent PTZ commands (move, zoom, etc.) directly from the tool, and the camera acknowledged and responded correctly, but the streamed video never changed. My best guess: zoom/move in the web interface is only "simulated" client-side (e.g. cropping and shifting the already-received video), not something the camera hardware actually does. Because of this, the tool doesn't include ePTZ/zoom controls.

## Additional tools

`test_tone.py` is a debugging tool to check for issues between your microphone and the DCS-2230's audio output. It sends a synthetic test tone instead of live microphone audio, which helps isolate whether a problem is in the capture/mic path or in the streaming/codec path.

Example — play a 1000 Hz tone for 20 seconds:
```
python test_tone.py --codec G.726 --freq 1000 --duration 20
```

## License

MIT; see [LICENSE](LICENSE).

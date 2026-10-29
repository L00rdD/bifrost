# The hub is standard library Python, plus adb to drive the projector (Google TV), and for its
# screen on the dashboard the scrcpy server (pushed to the projector) and ffmpeg (H.264 to JPEG).
FROM python:3-alpine
RUN apk add --no-cache android-tools ffmpeg
# A new version: its sha256 is next to it on the release page; the hub passes the version to the server
ARG SCRCPY=5.0.1
ARG SCRCPY_SHA256=764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536
RUN wget -qO /opt/scrcpy-server.jar "https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY}/scrcpy-server-v${SCRCPY}" \
 && echo "${SCRCPY_SHA256}  /opt/scrcpy-server.jar" | sha256sum -c -
ENV SCRCPY_VERSION=${SCRCPY}

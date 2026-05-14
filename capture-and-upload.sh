#!/usr/bin/env bash
set -euo pipefail

readonly VIDEO_DEVICE="/dev/video0"
readonly VIDEO_SIZE="2048x1536"

readonly BUCKET=""
readonly PREFIX="v1"

configure_camera() {
  v4l2-ctl -d "${VIDEO_DEVICE}" \
    --set-ctrl=power_line_frequency=2 \
    --set-ctrl=white_balance_automatic=0 \
    --set-ctrl=white_balance_temperature=4600 \
    --set-ctrl=auto_exposure=1 \
    --set-ctrl=exposure_time_absolute=625 \
    --set-ctrl=gain=0 \
    --set-ctrl=sharpness=4
}

build_s3_uri() {
  local timestamp
  local object_key

  timestamp="$(date +%Y%m%d-%H%M%S)"
  object_key="${PREFIX}/image-${timestamp}.jpg"

  echo "s3://${BUCKET}/${object_key}"
}

capture_image() {
  ffmpeg \
    -loglevel error \
    -f v4l2 \
    -input_format mjpeg \
    -video_size "${VIDEO_SIZE}" \
    -i "${VIDEO_DEVICE}" \
    -frames:v 1 \
    -f image2pipe \
    -vcodec mjpeg \
    -
}

upload_image() {
  local s3_uri="$1"

  /usr/bin/s3cmd put \
    --mime-type="image/jpeg" \
    - \
    "${s3_uri}"
}

main() {
  local s3_uri

  configure_camera

  s3_uri="$(build_s3_uri)"
  capture_image | upload_image "${s3_uri}"
}

main "$@"


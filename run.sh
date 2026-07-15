#!/usr/bin/env bash
# Launch the UVULITES live counter display.
#
# The Raspberry Pi 4's V3D GPU only exposes desktop OpenGL 3.1, but raylib 6.0
# requests a 3.3 core context. Without these overrides, window creation fails
# with "GLX: Failed to create context: GLXBadFBConfig" and the app can't start.
# Telling Mesa to advertise 3.3 lets the 3.1 hardware satisfy raylib's request
# (the small 3.1 -> 3.3 feature gap is covered by V3D's GLES 3.1 support).
export MESA_GL_VERSION_OVERRIDE=3.3
export MESA_GLSL_VERSION_OVERRIDE=330

# Run from the repo dir so `odin run` builds here and the program resolves
# instagram_service/followers.json relative to the executable.
cd "$(dirname "$0")" || exit 1
exec odin run . "$@"

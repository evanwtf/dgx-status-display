# ESP-IDF project for the Waveshare ESP32-C6-Touch-AMOLED-2.16 status display.
cmake_minimum_required(VERSION 3.16)
# The firmware version. The board sends it in its hello line.
set(PROJECT_VER "0.1.0")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(dgx_status_display)

# cJSON recurses once per nesting level. frame_parse.c already refuses input
# deeper than 4; this caps cJSON itself at 8 as a second line of defense.
idf_component_get_property(json_lib json COMPONENT_LIB)
target_compile_definitions(${json_lib} PRIVATE CJSON_NESTING_LIMIT=8)

# ESP-IDF project for the Waveshare ESP32-C6-Touch-AMOLED-2.16 status display.
cmake_minimum_required(VERSION 3.16)
# The firmware version. The board sends it in its hello line.
set(PROJECT_VER "0.0.4")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(dgx_status_display)

# cJSON recurses once per nesting level. frame_parse.c already refuses input
# deeper than 4; this caps cJSON itself at 8 as a second line of defense.
idf_component_get_property(json_lib json COMPONENT_LIB)
target_compile_definitions(${json_lib} PRIVATE CJSON_NESTING_LIMIT=8)

# LVGL's assert handler: trap, so the panic handler saves a core dump and
# reboots. LVGL's default spins until the task watchdog fires. PUBLIC: main
# includes lv_assert.h through lvgl.h too. LVGL uses the macro as a whole
# statement, and a ';' does not survive CMake and ninja, so the statement
# is an if with an empty body.
idf_component_get_property(lvgl_lib lvgl__lvgl COMPONENT_LIB)
target_compile_definitions(${lvgl_lib} PUBLIC "LV_ASSERT_HANDLER=if((__builtin_trap(),1)){}")

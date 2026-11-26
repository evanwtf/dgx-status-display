idf_component_register(
    SRCS
        "main.c"
        "axp2101.c"
        "display.c"
        "serial_rx.c"
        "ui.c"
        "view.c"
        "fonts.c"
        "fonts/jbm_num_110.c"
        "fonts/jbm_num_82.c"
        "fonts/jbm_mix_66.c"
        "fonts/jbm_text_36.c"
        "logic/frame_parse.c"
        "logic/line_reader.c"
        "logic/link_state.c"
        "logic/fmt.c"
        "logic/burnin.c"
        "logic/tiles.c"
        "logic/carousel.c"
        "logic/report.c"
    INCLUDE_DIRS "." "logic"
    REQUIRES
        esp_driver_i2c
        esp_driver_spi
        esp_driver_gpio
        esp_driver_usb_serial_jtag
        esp_lcd
        esp_timer
        esp_app_format
        json
        espcoredump
)
# Our own code builds with warnings as errors. Vendor components do not.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)

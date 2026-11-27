# dgx-status-display: a thin wrapper over uv and scripts/. Run `make` to list
# the targets. The logic lives in the scripts; the image pin lives in
# scripts/firmware.py only. Every target works from any directory.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules --no-print-directory

# The checkout that holds this Makefile, with no trailing slash. Make splits
# words on spaces, so a path with a space cannot work: refuse it here.
empty :=
space := $(empty) $(empty)
ifneq ($(words $(MAKEFILE_LIST)),1)
$(error The path to this Makefile holds a space: $(MAKEFILE_LIST). Move the checkout to a path with no space)
endif
REPO := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
ifneq ($(findstring $(space),$(REPO)),)
$(error The checkout path holds a space: $(REPO). Move the checkout to a path with no space)
endif
ifeq ($(wildcard $(REPO)/scripts/firmware.py),)
$(error No $(REPO)/scripts/firmware.py: this Makefile must sit in a dgx-status-display checkout)
endif
UV := cd "$(REPO)" && uv run --frozen
FW := $(UV) python scripts/firmware.py

# Board port: a /dev/serial/by-id/ path, or a glob that matches one port.
PORT ?=
# Optional ELF for `make coredump` (the `elf` value from the board's hello).
ELF ?=
# For `make release-check`, for example 0.0.2.
VERSION ?=
# NO_BACKUP=1: flash with no checked factory backup (not recommended).
NO_BACKUP ?=
# FORCE=1: install-service replaces a unit that serves another checkout.
FORCE ?=
SERVICE := dgx-status
UNIT := /etc/systemd/system/$(SERVICE).service
SERVICE_USER ?= $(or $(USER),$(shell id -un))

# The one port that PORT names. Make expands this when it expands the recipe,
# before any line runs (also under `make -n`), so a board target stops here.
board_port = $(strip \
  $(if $(PORT),,$(error PORT is not set. Set it to the board's port: export PORT=$$(ls /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00))) \
  $(if $(filter 1,$(words $(wildcard $(PORT)))),$(wildcard $(PORT)),$(error PORT=$(PORT) matches $(words $(wildcard $(PORT))) devices; it must match exactly one)))

# The account in the unit. Empty or root means make runs under sudo: refuse.
service_user = $(strip \
  $(if $(filter-out root,$(SERVICE_USER)),$(SERVICE_USER),$(error SERVICE_USER is "$(SERVICE_USER)". Run make as the account that runs the daemon and not under sudo; make asks for sudo itself)))

.PHONY: help setup check test \
  fw-test fw-build fw-size fw-preview fw-merge fonts-check readme-images \
  backup flash flash-merged coredump monitor \
  run dry-run install-service uninstall-service release-check clean

##@ Setup and checks

help: ## List the targets (the default)
	@awk 'BEGIN { FS = ":.*## " } \
	  /^##@ / { printf "\n%s\n", substr($$0, 5); next } \
	  /^[a-z][a-z0-9-]*:.*## / { printf "  %-18s %s\n", $$1, $$2 }' "$(firstword $(MAKEFILE_LIST))"
	@printf '\nBoard targets need PORT=/dev/serial/by-id/... and a stopped service.\n'

setup: ## Install the locked dependencies and the pre-commit hooks
	cd "$(REPO)" && uv sync --frozen
	cd "$(REPO)" && uv run --frozen pre-commit install

check: ## Run CI's Python checks (ruff, mypy, pytest, no skips); report every failure
	$(UV) python scripts/run_checks.py

test: ## Run pytest only
	$(UV) pytest -q -rs

##@ Firmware (in the pinned espressif/idf image)

fw-test: ## Run the C host unit tests (ASan, UBSan)
	$(FW) test

fw-build: ## Build the firmware (idf.py build)
	$(FW) build

fw-size: ## Show the firmware size (after fw-build)
	$(FW) size

fw-preview: ## Render the UI to firmware/preview/*.png
	$(FW) preview

fw-merge: ## Merge the build into one image for 0x0 (after fw-build)
	$(FW) merge

fonts-check: ## Check that the fonts regenerate byte for byte
	$(UV) python scripts/gen_fonts.py --check

readme-images: ## Render the preview, then write the README images to docs/img/
	$(FW) preview
	$(UV) python scripts/readme_images.py

##@ Board (PORT=...; esptool resets the chip; stop the service first)

backup: ## Read the whole 16 MB flash to backup/factory-16MB.bin and .sha256
	$(FW) backup --port "$(board_port)"

flash: ## Flash the build with flash_args; keeps the settings (needs the backup)
	$(FW) flash --port "$(board_port)"$(if $(NO_BACKUP), --no-backup)

flash-merged: ## First install: merge, then flash at 0x0; ERASES the settings (needs the backup)
	$(FW) merge
	$(FW) flash-merged --port "$(board_port)"$(if $(NO_BACKUP), --no-backup)

coredump: ## Read and decode the board's core dump (ELF=... for an older build)
	$(FW) coredump --port "$(board_port)"$(if $(ELF), --elf "$(ELF)")

monitor: ## Show the board's log with idf.py monitor (Ctrl-] quits)
	$(FW) monitor --port "$(board_port)"

##@ Daemon

run: ## Run the daemon in the foreground at INFO (PORT=... pins a port)
	cd "$(REPO)" && DGX_STATUS_LOG_LEVEL=INFO uv run --frozen dgx-status$(if $(PORT), --device "$(board_port)")

dry-run: ## Log one frame; open no device
	$(UV) dgx-status --dry-run --once

##@ Service (uses sudo; prints each command first)

install-service: ## Install the unit for this user and checkout; enable and (re)start it
	@test -x "$(REPO)/.venv/bin/dgx-status" || { echo "no .venv: run make setup first" >&2; exit 1; }
	@unit="$$(mktemp)"; trap 'rm -f "$$unit"' EXIT; \
	cd "$(REPO)"; \
	uv run --frozen python scripts/service.py render --user "$(service_user)" --repo "$(REPO)" \
	  --installed "$(UNIT)" --out "$$unit"$(if $(FORCE), --force); \
	echo "+ sudo install -m 0644 $$unit $(UNIT)"; \
	sudo install -m 0644 "$$unit" "$(UNIT)"
	sudo systemctl daemon-reload
	sudo systemctl enable $(SERVICE)
	sudo systemctl restart $(SERVICE)
	@echo "Follow the log: journalctl -u $(SERVICE) -f"

uninstall-service: ## Stop, disable and remove the systemd unit
	sudo systemctl disable --now $(SERVICE)
	sudo rm -f "$(UNIT)"
	sudo systemctl daemon-reload

##@ Release and cleanup

release-check: ## Check VERSION=x.y.z: the three versions and the changelog notes
	$(if $(VERSION),,$(error VERSION is not set, for example: make release-check VERSION=0.0.2))
	$(UV) python scripts/release_check.py version "v$(VERSION)"
	@notes="$$(mktemp)"; trap 'rm -f "$$notes"' EXIT; \
	cd "$(REPO)"; uv run --frozen python scripts/release_check.py notes "$(VERSION)" "$$notes"; \
	cat "$$notes"

clean: ## Remove build outputs (git-ignored files only); keeps backup/ and firmware/elf/
	cd "$(REPO)" && git clean -f -d -X -- firmware/build firmware/test/build firmware/test/preview/build firmware/preview dist

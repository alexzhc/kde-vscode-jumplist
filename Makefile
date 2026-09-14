PY ?= python3

# Every setting -- where the tool keeps its own files, which VS Code data is
# read, how the menu is laid out -- lives in one TOML file, so there is nothing
# to export here. ./config.toml is the checkout's copy; it documents every
# setting and its default, and `install` writes that same file to
# ~/.config/kde-vscode-jumplist if it is not there yet.
#
# Aim a single run at another file with `make update CONFIG=/tmp/other.toml`.
CONFIG ?= $(CURDIR)/config.toml

# Run the CLI from this checkout (pytest finds src/ via pyproject.toml). The
# configuration file is named outright, so a target does not depend on the
# working directory it happens to be run from.
CLI := PYTHONPATH=$(CURDIR)/src $(PY) -m kde_vscode_jumplist --config $(CONFIG)

# The unit written by `install`: one long-running service that watches VS Code's
# history and keeps the menu in step. It has an [Install] section, so
# `systemctl enable` on it is meaningful (unlike the oneshot+timer pair it
# replaced).
SYSTEMCTL ?= systemctl --user
APP := kde-vscode-jumplist
SERVICE := $(APP).service

.PHONY: help build recent r update watch pin unpin pinned f manage test install install-config uninstall reset enable disable

help:
	@echo "kde-vscode-jumplist"
	@echo ""
	@echo "  make build           build bin/kde-vscode-jumplist (single-file executable)"
	@echo "  make recent          refresh from VS Code and list recent entries"
	@echo "  make update          regenerate the right-click context menu"
	@echo "  make watch           keep the menu updated, syncing every 5s"
	@echo "  make pin ID=<id>     pin an entry"
	@echo "  make unpin ID=<id>   unpin an entry"
	@echo "  make pinned          list pinned entries"
	@echo "  make manage          pin and reorder pinned entries in a dialog"
	@echo "  make test            run the test suite"
	@echo "  make install         install the executable + systemd user service"
	@echo "  make install-config  apply this config.toml to the copy the service reads"
	@echo "  make enable          enable --now the service (start watching)"
	@echo "  make disable         disable --now the service (stop watching)"
	@echo "  make uninstall       remove the executable and the systemd user service"
	@echo "  make reset           rebuild the generated code.desktop from the vendor"
	@echo "                       file (saved pinned entries are left alone)"
	@echo ""
	@echo "  CONFIG               configuration file to use, default config.toml in"
	@echo "                       this checkout, e.g. make update CONFIG=/tmp/x.toml"
	@echo "  WATCH                seconds between 'watch' passes, default 5, e.g."
	@echo "                       make watch WATCH=2"
	@echo "  ARGS                 extra flags for 'recent', e.g."
	@echo "                       make recent ARGS=--uri"

build:
	@$(PY) $(CURDIR)/tools/build_zipapp.py

recent:
	@$(CLI) recent $(ARGS)
r: recent

update:
	@$(CLI) update
	@echo "menu updated - right-click the VS Code task manager icon"

# Foreground loop; Ctrl-C stops it. WATCH=<seconds> changes the interval, and
# an empty WATCH leaves the CLI's own default in place.
watch:
	@$(CLI) update --watch $(WATCH)

pin:
	@test -n "$(ID)" || { echo "usage: make pin ID=<entry-id>   # ids: make recent"; exit 1; }
	@$(CLI) pin $(ID)

unpin:
	@test -n "$(ID)" || { echo "usage: make unpin ID=<entry-id>   # ids: make pinned"; exit 1; }
	@$(CLI) unpin $(ID)

pinned:
	@$(CLI) pinned
p: pinned

manage:
	@$(CLI) manage

test:
	@$(PY) -m pytest

# `install` seeds ~/.config/kde-vscode-jumplist/config.toml from $(CONFIG) when
# it does not exist yet, and points the unit at the user's own copy, so the
# service keeps working if this checkout later moves. It will not overwrite an
# existing one, so an edit made here afterwards is applied with `install-config`
# -- otherwise this file's settings are not the ones in effect, and the menu
# keeps being regenerated from the other.
install: build
	@$(CLI) install

install-config:
	@$(CLI) install-config

enable:
	@$(SYSTEMCTL) enable --now $(SERVICE)
	@echo "enabled $(SERVICE) - it watches VS Code's history continuously"
	@echo "follow it with: $(SYSTEMCTL) status $(SERVICE)"

disable:
	@$(SYSTEMCTL) disable --now $(SERVICE)
	@echo "disabled $(SERVICE) - the menu stays as it is until something updates it"

uninstall:
	@$(CLI) uninstall

# Repairs the menu file only: pinned.json and entries.json are untouched.
reset:
	@$(CLI) reset

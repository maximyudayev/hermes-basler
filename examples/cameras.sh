#!/bin/sh
. ../.venv/bin/activate
hermes-cli -o ./data --config_file cameras.yml --experiment project=Test type=Cameras trial=0

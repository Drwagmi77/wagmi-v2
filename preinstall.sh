#!/bin/bash
pip uninstall solana solana-py -y
pip install solders==0.16.0 solana-py==0.34.0 --force-reinstall

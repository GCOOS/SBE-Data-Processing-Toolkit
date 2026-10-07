#!/usr/bin/env bash
# Create a cruise folder from 01_CRUISE_FOLDER_TEMPLATE. Runs in any bash
# environment on Windows (Git Bash, MSYS2, Cygwin, or WSL). Sea-Bird batch
# files need Windows paths, so paths are converted with cygpath when
# available, or by rewriting the drive prefix (/mnt/d/..., /cygdrive/d/...,
# /d/...) otherwise.

to_windows_path() {
	local path="$1"
	local drive rest
	if command -v cygpath >/dev/null 2>&1; then
		cygpath -w "$path"
		return
	fi
	case "$path" in
		/mnt/[A-Za-z]|/mnt/[A-Za-z]/*)
			drive="${path:5:1}"
			rest="${path:6}"
			;;
		/cygdrive/[A-Za-z]|/cygdrive/[A-Za-z]/*)
			drive="${path:10:1}"
			rest="${path:11}"
			;;
		/[A-Za-z]|/[A-Za-z]/*)
			drive="${path:1:1}"
			rest="${path:2}"
			;;
		[A-Za-z]:*)
			drive="${path:0:1}"
			rest="${path:2}"
			;;
		*)
			echo "Error: cannot convert '$path' to a Windows path." >&2
			return 1
			;;
	esac
	rest="${rest//\//\\}"
	printf '%s:%s\n' "${drive^^}" "${rest:-\\}"
}

# Check if the argument was provided
if [ -z "$1" ]; then
	echo "Error: No folder name provided."
	echo "Usage: $0 <cruise_folder_name>"
	exit 1
fi
# 2. Check if it exists as a directory in the current folder
if [ -d "./$1" ]; then
	echo "Error: '$1' is already a subfolder in the current directory."
	exit 1
fi
if [ ! -d "./01_CRUISE_FOLDER_TEMPLATE" ]; then
	echo "Error: 01_CRUISE_FOLDER_TEMPLATE was not found in the current directory."
	exit 1
fi

workdir=$(pwd)
cruisedir="$workdir/$1"
echo "Cruise directory: $cruisedir"
# get path in Windows format for SBEDataProcessing SW
win_path_cruise_dir=$(to_windows_path "$cruisedir") || exit 1
# double the backslashes
win_cruise_dir="${win_path_cruise_dir//\\/\\\\}"
# Create new cruise folder from the template
cp -pr 01_CRUISE_FOLDER_TEMPLATE "$1"
# Modify the SBE Data Processing SW batch file templates
sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" "$1/CNV/batch.txt"
sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" "$1/UPDOWN/batch.txt"
echo "DONE."

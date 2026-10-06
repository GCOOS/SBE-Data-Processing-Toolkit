#!/usr/bin/env bash
# Conductivity alignment experiment that runs in any bash environment on
# Windows (Git Bash, MSYS2, Cygwin, or WSL). SBEBatch.exe needs Windows paths,
# so paths are converted with cygpath when available, or by rewriting the
# drive prefix (/mnt/d/..., /cygdrive/d/..., /d/...) otherwise.

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

# SBE911Plus default alignment for Conductivity is 0.073 seconds.
# The default experiment range brackets it.
DEFAULT_START=0.03
DEFAULT_END=0.09
DEFAULT_COUNT=4

usage() {
	echo "Usage: $0 <cruise_folder_name> [<start_delay> <end_delay> <number_of_values>]"
	echo "  Delays are in seconds. The tested values are spread evenly from start to end,"
	echo "  including both. Default: $DEFAULT_START to $DEFAULT_END with $DEFAULT_COUNT values."
}

### Cruise name and optional delay range as parameters
# 1. Check the arguments
if [ -z "$1" ]; then
	echo "Error: No folder name provided."
	usage
	exit 1
fi
if [ $# -ne 1 ] && [ $# -ne 4 ]; then
	echo "Error: Give the cruise folder name alone, or followed by the start delay, end delay, and number of values."
	usage
	exit 1
fi
start_delay="${2:-$DEFAULT_START}"
end_delay="${3:-$DEFAULT_END}"
delay_count="${4:-$DEFAULT_COUNT}"
number_pattern='^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
for value in "$start_delay" "$end_delay"; do
	if [[ ! $value =~ $number_pattern ]]; then
		echo "Error: '$value' is not a valid delay value."
		usage
		exit 1
	fi
done
if [[ ! $delay_count =~ ^[1-9][0-9]*$ ]]; then
	echo "Error: The number of values must be a positive whole number, not '$delay_count'."
	usage
	exit 1
fi
if ((delay_count == 1)) && LC_ALL=C awk -v s="$start_delay" -v e="$end_delay" 'BEGIN { exit !(s + 0 != e + 0) }'; then
	echo "Error: With one value, the start and end delays must be the same."
	exit 1
fi
# 2. Check if it exists as a directory in the current folder
if [ ! -d "./$1" ]; then
	echo "Error: '$1' is not a subfolder in the current directory."
	exit 1
fi
if ! command -v SBEBatch.exe >/dev/null 2>&1; then
	echo "Error: SBEBatch.exe was not found. Add the Sea-Bird SBE Data Processing folder to PATH."
	exit 1
fi
workdir=$(pwd)
cruisedir="$workdir/$1"
echo "Cruise directory: $cruisedir"

### get path in Windows format for SBEDataProcessing SW
win_path_cruise_dir=$(to_windows_path "$cruisedir") || exit 1
# double (escape) the backslashes
win_cruise_dir="${win_path_cruise_dir//\\/\\\\}"

shopt -s nullglob nocaseglob
xmlcon_files=( "$cruisedir"/CNV/05-loop/*.xmlcon )
psa_files=( "$cruisedir"/CNV/*.psa )
shopt -u nullglob nocaseglob
if ((${#xmlcon_files[@]} == 0)); then
	echo "Error: No .xmlcon files found in $cruisedir/CNV/05-loop."
	exit 1
fi
if ((${#psa_files[@]} == 0)); then
	echo "Error: No .psa files found in $cruisedir/CNV."
	exit 1
fi


DELAYS=$(LC_ALL=C awk -v s="$start_delay" -v e="$end_delay" -v n="$delay_count" 'BEGIN {
	for (i = 0; i < n; i++) {
		v = (n == 1) ? s : s + (e - s) * i / (n - 1)
		printf "%.6f\n", v
	}
}')
if [ -n "$(printf '%s\n' $DELAYS | sort | uniq -d)" ]; then
	echo "Error: The delay values are too close together to tell apart: $(echo $DELAYS)"
	exit 1
fi
echo "Conductivity delays to test (seconds): $(echo $DELAYS)"

# cp -pr would nest TEMPLATE inside an existing folder, and batch.txt
# placeholders would already be replaced, so earlier results must be removed.
for DELAY in $DELAYS; do
	if [ -e "${cruisedir}/ALIGN_TC/OUT/${DELAY}" ]; then
		echo "Error: ${cruisedir}/ALIGN_TC/OUT/${DELAY} already exists."
		echo "Remove or rename the earlier results in ALIGN_TC/OUT before rerunning."
		exit 1
	fi
done

for DELAY in $DELAYS; do
	export DELAY

	# copy template to new dir named after the delay value
	delay_dir="${cruisedir}/ALIGN_TC/OUT/${DELAY}"
	cp -pr "${cruisedir}/ALIGN_TC/TEMPLATE" "$delay_dir"

	# change to the new delay directory
	cd "$delay_dir" || exit 1

	## Copy files from CNV folder:
	# 1) Seabird config files
	cp "${xmlcon_files[@]}" ./05-loop/
	# 2) .psa files
	cp "${psa_files[@]}" .
	# not needed here
	rm -f DatCnv.psa

	# modify the input and output directories on the copied psa files
	win_delaydir=$(to_windows_path "$delay_dir") || exit 1

	# 1) AlignCTD.psa:
	align_indir="$win_delaydir\02-flt"
	export align_indir
	### CAN TAKE FROM ORIG CNV DIRECTORY!!! perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_indir} . $2}e' AlignCTD.psa
	align_outdir="$win_delaydir\03-aln"
	export align_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_outdir} . $2}e' AlignCTD.psa

	perl -pi -e '
		if (/<ValArrayItem\b/ && /\bvariable_name="Conductivity"/) {
		 s{(\bvalue=")[^"]*(")}{$1 . $ENV{DELAY} . $2}e;
	}
	' AlignCTD.psa

	# 2) CellTM.psa
	cell_indir="$win_delaydir\03-aln"
	export cell_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_indir} . $2}e' CellTM.psa
	cell_outdir="$win_delaydir\04-cel"
	export cell_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_outdir} . $2}e' CellTM.psa

	# 3) LoopEdit.psa
	loop_indir="$win_delaydir\04-cel"
	export loop_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_indir} . $2}e' LoopEdit.psa
	loop_outdir="$win_delaydir\05-loop"
	export loop_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_outdir} . $2}e' LoopEdit.psa

	# 4) Derive.psa
	derive_indir="$win_delaydir\05-loop"
	export derive_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_indir} . $2}e' Derive.psa
	derive_outdir="$win_delaydir\06-drv"
	export derive_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_outdir} . $2}e' Derive.psa

	# get a master xmlcon config file if found:
	## shopt -s nullglob nocaseglob
	## files=( "$delay_dir"/05-loop/*master*.xmlcon )
	## if ((${#files[@]} > 0)); then
	##	master_file="${files[0]}"
	##	echo "$master_file"
	##	win_master_cfg=$(to_windows_path "$master_file") || exit 1
	##	export win_master_cfg
	##	perl -pi -e 's{(<InstrumentPath\b[^>]*value=")[^"]*(")}{$1 . $ENV{win_master_cfg} . $2}e' Derive.psa
	##else
	##	echo "No master .xmlcon file found"
	##fi
	##shopt -u nullglob nocaseglob

	# modify batch template file
	sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" batch.txt
	sed -i "s/_DELAY_/$DELAY/g" batch.txt

	# run SBEDataProcessing, all the steps, batch mode
	SBEBatch.exe batch.txt

	#read -p "Press [Enter] to continue..."

	cd "$workdir" || exit 1
done

echo "DONE."

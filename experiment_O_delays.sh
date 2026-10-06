#!/usr/bin/env bash
# Oxygen alignment experiment that runs in any bash environment on Windows
# (Git Bash, MSYS2, Cygwin, or WSL). SBEBatch.exe needs Windows paths, so
# paths are converted with cygpath when available, or by rewriting the drive
# prefix (/mnt/d/..., /cygdrive/d/..., /d/...) otherwise.

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

### Cruise name as parameter
# 1. Check if the argument was provided
if [ -z "$1" ]; then
	echo "Error: No folder name provided."
	echo "Usage: $0 <cruise_folder_name>"
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
updown_cnv_files=( "$cruisedir"/UPDOWN/01-cnv/*.cnv )
shopt -u nullglob nocaseglob
if ((${#xmlcon_files[@]} == 0)); then
	echo "Error: No .xmlcon files found in $cruisedir/CNV/05-loop."
	exit 1
fi
if ((${#psa_files[@]} == 0)); then
	echo "Error: No .psa files found in $cruisedir/CNV."
	exit 1
fi
if ((${#updown_cnv_files[@]} == 0)); then
	echo "Error: No .cnv files found in $cruisedir/UPDOWN/01-cnv."
	echo "Run the Convert step in UPDOWN (down- and upcast) first."
	exit 1
fi


# SBE911Plus default alignment for Oxygen is 3.5 seconds.
# Experiment around it:
#DELAYS=$(seq -f "%.1f" 2 0.5 6)
DELAYS=$(seq -f "%.1f" 2 1 6)

# cp -pr would nest TEMPLATE inside an existing folder, and batch.txt
# placeholders would already be replaced, so earlier results must be removed.
for DELAY in $DELAYS; do
	if [ -e "${cruisedir}/ALIGN_O/OUT/${DELAY}" ]; then
		echo "Error: ${cruisedir}/ALIGN_O/OUT/${DELAY} already exists."
		echo "Remove or rename the earlier results in ALIGN_O/OUT before rerunning."
		exit 1
	fi
done

for DELAY in $DELAYS; do
	export DELAY

	# copy template to new dir named after the delay value
	delay_dir="${cruisedir}/ALIGN_O/OUT/${DELAY}"
	cp -pr "${cruisedir}/ALIGN_O/TEMPLATE" "$delay_dir"

	# change to the new delay directory
	cd "$delay_dir" || exit 1

	## Copy files from CNV folder:
	# 1) Seabird config files
	cp "${xmlcon_files[@]}" ./05-loop/
	# 2) .psa files
	cp "${psa_files[@]}" .
	# not needed (using already converted files from UPDOWN ...) :
	rm -f DatCnv.psa

	# modify the input and output directories on the copied psa files
	win_delaydir=$(to_windows_path "$delay_dir") || exit 1

	# 1) Filter.psa:
	# Take input files from orig UPDOWN dir
	filter_indir="$win_path_cruise_dir\UPDOWN\01-cnv"
	export filter_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{filter_indir} . $2}e' Filter.psa
	filter_outdir="$win_delaydir\02-flt"
	export filter_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{filter_outdir} . $2}e' Filter.psa

	# 2) AlignCTD.psa:
	align_indir="$win_delaydir\02-flt"
	export align_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_indir} . $2}e' AlignCTD.psa
	align_outdir="$win_delaydir\03-aln"
	export align_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_outdir} . $2}e' AlignCTD.psa

	perl -pi -e '
		if (/<ValArrayItem\b/ && /\bvariable_name="Oxygen raw, SBE 43"/) {
		 s{(\bvalue=")[^"]*(")}{$1 . $ENV{DELAY} . $2}e;
	}
	' AlignCTD.psa

	# 3) CellTM.psa
	cell_indir="$win_delaydir\03-aln"
	export cell_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_indir} . $2}e' CellTM.psa
	cell_outdir="$win_delaydir\04-cel"
	export cell_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_outdir} . $2}e' CellTM.psa

	# 4) LoopEdit.psa
	loop_indir="$win_delaydir\04-cel"
	export loop_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_indir} . $2}e' LoopEdit.psa
	loop_outdir="$win_delaydir\05-loop"
	export loop_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_outdir} . $2}e' LoopEdit.psa

	# 5) Derive.psa
	derive_indir="$win_delaydir\05-loop"
	export derive_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_indir} . $2}e' Derive.psa
	derive_outdir="$win_delaydir\06-drv"
	export derive_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_outdir} . $2}e' Derive.psa

	# get a master xmlcon config file if found:
	shopt -s nullglob nocaseglob
	files=( "$delay_dir"/05-loop/*master*.xmlcon )
	if ((${#files[@]} > 0)); then
		master_file="${files[0]}"
		echo "$master_file"
		win_master_cfg=$(to_windows_path "$master_file") || exit 1
		export win_master_cfg
		perl -pi -e 's{(<InstrumentPath\b[^>]*value=")[^"]*(")}{$1 . $ENV{win_master_cfg} . $2}e' Derive.psa
	else
		echo "No master .xmlcon file found"
	fi
	shopt -u nullglob nocaseglob

	# modify batch template file
	sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" batch.txt
	sed -i "s/_DELAY_/$DELAY/g" batch.txt

	# run SBEDataProcessing, all the steps, batch mode
	SBEBatch.exe batch.txt

	#read -p "Press [Enter] to continue..."

	cd "$workdir" || exit 1
done

echo "DONE."

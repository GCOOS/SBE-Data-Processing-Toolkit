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
workdir=$(pwd)
cruisedir="$workdir/$1"
echo "Cruise directory: $cruisedir"

### get path in Windows format for SBEDataProcessing SW
wsl_cruise_dir=$(wslpath -w $cruisedir)
# double (escape) the backslashes
win_cruise_dir="${wsl_cruise_dir//\\/\\\\}"


# SBE911Plus default alignment for Oxygen is 3.5 seconds.
# Experiment around it:
#for DELAY in $(seq -f "%.1f" 2 0.5 6); do
for DELAY in $(seq -f "%.1f" 2 1 6); do

	export DELAY
	
	# copy template to new dir named after the delay value
	delay_dir="${cruisedir}/ALIGN_O/OUT/${DELAY}"
	cp -pr "${cruisedir}/ALIGN_O/TEMPLATE" "$delay_dir"
	
	# change to the new delay directory
	cd "$delay_dir"
	
	## Copy files from CNV folder:
	# 1) Seabird config files
    cp ${cruisedir}/CNV/05-loop/*.xmlcon ./05-loop/
	cp ${cruisedir}/CNV/05-loop/*.XMLCON ./05-loop/
	# 2) .psa files
	cp ${cruisedir}/CNV/*.psa .
	# not needed (using already converted files from UPDOWN ...) :
	rm DatCnv.psa
	
	# modify the input and output directories on the copied psa files
	wsl_delaydir=$(wslpath -w $delay_dir)

	# 1) Filter.psa: 
	# Take input files from orig UPDOWN dir
	filter_indir="$wsl_cruise_dir\UPDOWN\01-cnv"
	export filter_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{filter_indir} . $2}e' Filter.psa
	filter_outdir="$wsl_delaydir\02-flt"
	export filter_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{filter_outdir} . $2}e' Filter.psa

	# 2) AlignCTD.psa:
	align_indir="$wsl_delaydir\02-flt"
	export align_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_indir} . $2}e' AlignCTD.psa
	align_outdir="$wsl_delaydir\03-aln"
	export align_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{align_outdir} . $2}e' AlignCTD.psa
	
	perl -pi -e '
		if (/<ValArrayItem\b/ && /\bvariable_name="Oxygen raw, SBE 43"/) {
		 s{(\bvalue=")[^"]*(")}{$1 . $ENV{DELAY} . $2}e;
	}
	' AlignCTD.psa
	
	# 3) CellTM.psa
	cell_indir="$wsl_delaydir\03-aln"
	export cell_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_indir} . $2}e' CellTM.psa
	cell_outdir="$wsl_delaydir\04-cel"
	export cell_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{cell_outdir} . $2}e' CellTM.psa
	
	# 4) LoopEdit.psa
	loop_indir="$wsl_delaydir\04-cel"
	export loop_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_indir} . $2}e' LoopEdit.psa
	loop_outdir="$wsl_delaydir\05-loop"
	export loop_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{loop_outdir} . $2}e' LoopEdit.psa
	
	# 5) Derive.psa
	derive_indir="$wsl_delaydir\05-loop"
	export derive_indir
	perl -pi -e 's{(<InputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_indir} . $2}e' Derive.psa
	derive_outdir="$wsl_delaydir\06-drv"
	export derive_outdir
	perl -pi -e 's{(<OutputDir\b[^>]*value=")[^"]*(")}{$1 . $ENV{derive_outdir} . $2}e' Derive.psa
	
	# get a master xmlcon config file if found:
	shopt -s nullglob nocaseglob
	files=( "$delay_dir"/05-loop/*master*.xmlcon )
	if ((${#files[@]} > 0)); then
		master_file="${files[0]}"
		echo "$master_file"
		wsl_master_cfg=$(wslpath -w $master_file)
		export wsl_master_cfg
		perl -pi -e 's{(<InstrumentPath\b[^>]*value=")[^"]*(")}{$1 . $ENV{wsl_master_cfg} . $2}e' Derive.psa
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

	cd $workdir
done

echo "DONE."
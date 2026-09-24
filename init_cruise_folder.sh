# Takes cruise name as parameter, uses template folder to create the cruise folder

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

workdir=$(pwd)
cruisedir="$workdir/$1"
echo "Cruise directory: $cruisedir"
# get path in Windows format for SBEDataProcessing SW
wsl_cruise_dir=$(wslpath -w $cruisedir)
# double the backslashes
win_cruise_dir="${wsl_cruise_dir//\\/\\\\}"
# Create new cruise folder from the template
cp -pr 01_CRUISE_FOLDER_TEMPLATE $1
# Modify the SBE Data Processing SW batch file templates
sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" "$1/CNV/batch.txt"
sed -i "s/_CRUISEDIR_/$win_cruise_dir/g" "$1/UPDOWN/batch.txt"
echo "DONE."
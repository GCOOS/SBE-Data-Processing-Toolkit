Run the "experiment_TC_delays.sh" bash script on the "01-CONVERT_WORKDIR" level, give the cruise folder as parameter.
The script will copy folder structure from a template folder, and the .psa files and the 05-loop .xmlcon files from the cruise CNV folder.
The script also modifies the input and output paths inside those .psa files, as well as the Conductivity delay values inside the AlignCTD.psa file.
Input data is read from the cruise CNV/02-flt folder, which must contain both the downcast and the upcast.


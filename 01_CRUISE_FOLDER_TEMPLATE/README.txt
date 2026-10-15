FOLDER STRUCTURE:
------------------
- CRUISE_ORIG_DATA:	Place original CTD cast related notes and documents from the cruise under NOTES.
			Place raw CTD cast data, all files, CTD_DATA.

- CNV: 			Used for processing the cruise data. ALWAYS convert both the downcast and the upcast here.
			(Only downcast data is published on GCOOS ERDDAP, but converting both casts avoids losing
			 data at shallow stations where the Seabird SW fails to detect the downcast reliably.)
			OBS! The user may need to process the data more than once in this folder if the Conductivity, Oxygen 
			alignment values need to be adjusted.
		
- UPDOWN: 		Optional scratch folder for separate down- and upcast processing experiments.
			It is no longer part of the standard workflow, because CNV already contains both casts.

- ALIGN_TC: 		Used for figuring out correct Conductivity alignment value.
			Input data is read from the CNV/02-flt folder.
			User needs to set up the .psa template files correctly first, see README.txt under this folder. 
			The bash script "experiment_TC_delays.sh" under "01-CONVERT_WORKDIR" folder uses the templates
			to batch process the data with several different alignment values.

- ALIGN_O		Used for figuring out correct Oxygen (ml-l) alignment value. 
			Down- and upcast data is required, and it is read from the CNV/01-cnv folder.
			User needs to set up the .psa template files correctly first, see README.txt under this folder. 
			The bash script "experiment_O_delays.sh" under "01-CONVERT_WORKDIR" folder uses the templates
			to batch process the data with several different alignment values.


SUGGESTED PROCESSING ORDER:
---------------------------
1) Process the data under the CNV folder with default alignment values, ALWAYS including both the downcast
   and the upcast. Make sure to include the "pump status" variable!
2) Use pump_status_report.py Python script under 01-CONVERT_WORKDIR to review the data quality and to check
	if for any stations the pump never turns on.
	(because some stations are so shallow, there is no cast.)

3) IF CONDUCTIVITY ALIGNMENT VALUE NEEDS TO BE EXPLORED: 
	- prepare the ALIGN_TC folder according to instructions there
	- run experiment_TC_delays.sh script according to the above instructions
4) IF OXYGEN ALIGNMENT VALUE NEEDS TO BE EXPLORED: 
	- prepare the ALIGN_O folder according to instructions there
	- run experiment_O_delays.sh script according to the above instructions
5) IF ANY ALIGNMENT VALUES NEEDED CHANGING / EXPLORING:
	- re-run the conversion under CNV with the chosen values, again including both the downcast and the upcast

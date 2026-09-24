FOLDER STRUCTURE:
------------------
- CRUISE_ORIG_DATA:	Place original CTD cast related notes and documents from the cruise under NOTES.
			Place raw CTD cast data, all files, CTD_DATA.

- CNV: 			Used for processing the downcast data for the cruise. (Only downcast data is published on GCOOS ERDDAP.)
			OBS! The user may need to process the data more than once in this folder if the Conductivity, Oxygen 
			alignment values need to be adjusted.
		
- UPDOWN: 		Used for processing the down- and upcast data for the cruise

- ALIGN_TC: 		Used for figuring out correct Conductivity alignment value.
			Downcast data can be copied over to this folder from CNV folder.
			User needs to set up the .psa template files correctly first, see README.txt under this folder. 
			The bash script "experiment_TC_delays.sh" under "01-CONVERT_WORKDIR" folder uses the templates
			to batch process the data with several different alignment values.

- ALIGN_O		Used for figuring out correct Oxygen (ml-l) alignment value. 
			Down- and upcast data is required, and it can be copied from UPDOWN folder.
			User needs to set up the .psa template files correctly first, see README.txt under this folder. 
			The bash script "experiment_O_delays.sh" under "01-CONVERT_WORKDIR" folder uses the templates
			to batch process the data with several different alignment values.


SUGGESTED PROCESSING ORDER:
---------------------------
1) Process downcast data with default alignment values under CNV folder. Make sure to include "pump status" variable!
2) Use pump_status_report.py Python script under 01-CONVERT_WORKDIR to check if for any stations pump never turns on!
	(because some sations are so shallow, there is no cast. Seabird SW sometimes only includes a few scans from 
	 the beginning of the cast, before the pumps are even turned on.)
	
  -> IF REPORT SHOWS PUMPS NEVER TURN ON FOR SOME STATIONS:
	- run up- and downcast data conversion under UPDOWN. Copy the .cnv files for the problem stations from 01-cnv from UPDOWN to 
	  CNV, and re-run the downcast data processing under CNV to make sure even the shallow stations with no cast include valid data where pumps are on and sensors start producing valid
          readings.

3) IF CONDUCTIVITY ALIGNMENT VALUE NEEDS TO BE EXPLORED: 
	- prepare the ALIGN_TC folder according to instructions there
	- run experiment_TC_delays.sh script according to the above instructions
4) IF OXYGEN ALIGNMENT VALUE NEEDS TO BE EXPLORED: 
	- first run up-and down conversion under UPDOWN folder
	- prepare the ALIGN_O folder according to instructions there
	- run experiment_O_delays.sh script according to the above instructions
5) IF ANY ALIGNMENT VALUES NEEDED CHANGING / EXPLORING:


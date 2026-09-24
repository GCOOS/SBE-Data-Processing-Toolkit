Use this folder to convert both down- and upcast of the CTD data.

You can copy the .hex and .xmlcon files from CNV folder to 00-hex. 
Also copy the .xmlcon files to 05-loop.

If you want, you can also copy the .psa files (SBEDataProcessing SW project files) and batch file 
here from the CNV folder and modify DatCnv.psa to include both up- and downcast.

- DOWN AND UPCAST:
	<FromCast value="0" high="1" low="0" initialValue="0" />
- JUST DOWNCAST:
	<FromCast value="1" high="1" low="0" initialValue="0" />

Process the data either by running the SBEDataProcessing SW manually or in batch mode.

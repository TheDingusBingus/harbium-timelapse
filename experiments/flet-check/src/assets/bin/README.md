Put the Windows programs the experiments use here before building:

- exiftool.exe, plus the exiftool_files folder that comes with it
  (from the Windows Executable download at https://exiftool.org, renamed
  from "exiftool(-k).exe" to "exiftool.exe")
- ffmpeg.exe (from a Windows build such as https://www.gyan.dev/ffmpeg/builds/)

The app runs these copies by their full path inside the app, never from PATH.

# Windows runtime license sources

These verbatim Tcl/Tk 8.6 license files are release inputs for the pinned
Windows build. The release tool also copies the exact CPython and installed
Python-distribution license files from the isolated build environment.

- Tcl 8.6: <https://github.com/tcltk/tcl/blob/core-8-6-branch/license.terms>
- Tk 8.6: <https://github.com/tcltk/tk/blob/core-8-6-branch/license.terms>

If the build lock changes Tcl or Tk versions, replace the corresponding
license files from the exact upstream source revision before releasing.

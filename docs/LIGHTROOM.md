# Lightroom and Camera Raw

`timelapse prepare` can write deflicker corrections into Lightroom's `.xmp` sidecar files, on top of whatever exposure edits you've already made. This works for RAW files that have a separate `.xmp` file next to them (for example `IMG_1281.CR2` and `IMG_1281.xmp`).

Please treat this as experimental for now. It was built and tested against real Lightroom Classic sidecar files, and it only ever changes the Exposure value (everything else in the file, masks included, stays exactly as it was), but it hasn't yet been tried inside Lightroom itself. If you use Lightroom and give it a go, I'd really like to hear whether it worked.

## Why there are a couple of extra steps

Lightroom keeps your edits in its catalogue and only reads `.xmp` files when you ask it to. So the edits need saving out to the files first, and reading back in afterwards.

## Step by step

1. In Lightroom, select every frame of the sequence, then choose **Metadata > Save Metadata to Files** (Ctrl+S on Windows, Cmd+S on a Mac). This makes sure each `.xmp` file matches your current edits. Don't skip it: if the files are out of date, reading them back in later would bring back the old edits.
2. Close Lightroom.
3. Run:
   ```bash
   timelapse prepare "path/to/your/raw folder" --dry-run   # see what it would do first
   timelapse prepare "path/to/your/raw folder"
   ```
4. Open Lightroom, select every frame again, and choose **Metadata > Read Metadata from Files**. The new exposures should now show up.
5. Export every frame, then run `timelapse finish` on the export as usual.

To take the corrections back out, close Lightroom, run `timelapse undo "path/to/your/raw folder"`, and read the metadata from the files again.

## What it doesn't do yet

- **White balance.** Lightroom works out its temperature figures from Adobe's own camera profiles, which don't match the camera's figures exactly, and I haven't had a copy of Lightroom to check the conversion against. For now white balance is left as you set it, and `timelapse finish` evens out any colour shifts after export.
- **DNG, JPEG and TIFF files.** For these, Lightroom stores its settings inside the file rather than in a separate `.xmp`, and they aren't supported yet.
- **Camera Raw in Photoshop or Bridge** uses the same `.xmp` files and should work the same way, but hasn't been tested either.

## If something looks wrong

- Every `.xmp` file is backed up before its first change, in a hidden `.timelapse-core` folder inside the RAW folder.
- `timelapse undo` takes out exactly what `prepare` added, and leaves the files as they were before, byte for byte.
- Copies like `IMG_1273_1.xmp` that don't belong to a photo are ignored, and darktable's `.xmp` files (named like `IMG_1281.CR2.xmp`) are never touched.

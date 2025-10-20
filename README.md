# Portfolio: Eric Chan
## About me
Hi I am Eric! I'm a graduating masters student from Animal Logic Academy, specialising in both FX and pipeline TD. Prior to this, I was an OS engineer at seL4 Trustworthy Systems, a research group in UNSW who maintain and develop the seL4 microkernel. I did my undergrad in computer science majoring in AI at UNSW. I have an avid interest in designing systems and also storytelling, which has led me to the unique place of film.

## Past work experience
### OS Engineer - 2023-2024
I was an OS engineer at seL4 Trustworthy Systems for 2 years, doing various kernel-level work. I was recruited to work on their new OS framework Microkit, built-thinly on top of the underlying seL4 microkernel. Its purpose was to help system designers create static software systems on top the minimally built seL4 microkernel. My work in Microkit mainly involved maintaining their hypervisor subsystem [libvmm](https://github.com/au-ts/libvmm) for aarch64, and their various [device driver frameworks](https://github.com/au-ts/sddf). In particular, I was the primary developer and designer for their graphics protocol ([link to PR](https://github.com/au-ts/sddf/pull/242)).

## UTS Masters of Animation and Visualisation at ALA, FX & Pipeline TD - 2025

### FX Showreel

![reel](reel.gif)
https://vimeo.com/1138123238
- CFX uses multi-shot workflow, published for all ~30 shots of the film. This was my first Houdini project as a complete CG beginner, took me 3 months to learn the software and complete it.
- Oniko is ongoing until Dec 05, 2025

### Pipeline TD

- Developed tooling for artists across multiple DCCs (Maya, Houdini, Nuke, Katana). UIs were done in Qt.
- Various help here and there as IT support. Debugging tractor render farm and troubleshooting ShotGrid/USD for fellow artists.
- General improvements to ALA's USD native pipeline, such as extending their comp builder in Nuke for extra pass types, adding FX asset variant sets to USD etc.

More details in `pipeline/` README.md, and it also holds gifs of some of the scripts that I've written.

### Personal projects
####  Re-implementing Houdini's Peak and benchmarking it
This project re-implements the peak node functionality from Houdini (literally, just the displacement of points along normals) using various implementations as well as benchmarking their performance. My goals for this project was to pick a simple enough task to learn C++ and gain familiarity with the various C++ centric frameworks and libraries used frequently in film and graphics. These include:
- USD
- Eigen
- oneTBB
- CUDA
- Tracy profiling
- CMake

Took 5 weekends to complete. https://github.com/erichchan999/deformer/tree/main

## UNSW Bachelor of Computer Science - 2020-2022

Below are some projects I've worked on during my undergrad. Contact me for full transcript.

### NLP Sentiment Analysis
This project classifies movie review text as positive or negative using various natural language models, completed pre-LLM era.
https://github.com/erichchan999/movie_review_nlp/blob/main/Analysis.ipynb

### Frontend Kahoot clone
This project uses React to remake the minimal features of kahoot quiz app.
https://github.com/erichchan999/kahoot_clone




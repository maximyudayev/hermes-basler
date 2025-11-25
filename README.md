# HERMES - Basler Cameras

Support package to interface the GigE Vision [Basler ace 2](https://pupil-labs.com/products/core) commercial CMOS cameras in [HERMES](https://github.com/maximyudayev/hermes), like the [a2a2590-22gcBAS](https://docs.baslerweb.com/a2a2590-22gcbas).

## Installation
Node available under the same HERMES namespace of `hermes.basler` package upon installation, as `CameraProducer`.

### From PyPI
```bash
pip install pysio-hermes-basler
```

### From source
```bash
git clone https://github.com/maximyudayev/hermes-basler.git
pip install -e hermes-basler
```

## Usage
Physical connection requires a PCIe PoE NIC, like the [one distributed by Basler](https://www.baslerweb.com/en/shop/basler-gige-interface-card-4-port-poe/). The interface and control is done through the OEM Python SDK integrated into this package.

Configure the cameras in the [Pylon Viewer](https://www.baslerweb.com/en/software/pylon/pylon-viewer/) application once, to ensure the camera settings are set to reach the desired frame rate and resolution. Save the settings as a "User Set" for the cameras to autoload the preset. 

Using the device follows the standard [configuration file specification](https://maximyudayev.github.io/hermes/) process of HERMES nodes.

## Citation
When using any parts of this repository outside of its intended use, please cite the parent project [HERMES](https://github.com/maximyudayev/hermes).

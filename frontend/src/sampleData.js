export const SAMPLE_QUERY =
  "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است را به پلیگون تبدیل کن";

export const SAMPLE_INPUTS = {
  raster: {
    data: [
      [
        [1, 1, 1],
        [1, 1, 1],
      ],
      [
        [2, 1, 4],
        [1, 3, 0.5],
      ],
    ],
    metadata: {
      transform: [10, 0, 100, 0, -10, 200],
      crs: "EPSG:3857",
      nodata: -9999,
    },
  },
};

export const SAMPLE_BAND_MAP = {
  red: 1,
  nir: 2,
};

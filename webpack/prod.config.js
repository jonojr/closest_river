const { merge } = require('webpack-merge');
const commonConfig = require('./common.config');
const Dotenv = require('dotenv-webpack');
const path = require('node:path');

// This variable should mirror the one from config/settings/production.py
const staticUrl = '/static/';

module.exports = merge(commonConfig, {
  mode: 'production',
  devtool: 'source-map',
  bail: true,
  output: {
    publicPath: `${staticUrl}webpack_bundles/`,
  },
  plugins: [
    // .envs/ is in .dockerignore, so inside the image build the file is
    // absent and MAPTILER_API_KEY comes from the build arg instead (see the
    // client-builder stage of compose/production/django/Dockerfile).
    new Dotenv({
      path: path.resolve(__dirname, '../.envs/.production/.node'),
      systemvars: true,
    }),
  ],
});

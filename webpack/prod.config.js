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
    new Dotenv({ path: path.resolve(__dirname, '../.envs/.production/.node') }),
  ],
});

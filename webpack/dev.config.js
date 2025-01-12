const { merge } = require('webpack-merge');
const commonConfig = require('./common.config');
const Dotenv = require('dotenv-webpack');
const path = require('node:path');

module.exports = merge(commonConfig, {
  mode: 'development',
  devtool: 'inline-source-map',
  plugins: [
    new Dotenv({ path: path.resolve(__dirname, '../.envs/.local/.node') }),
  ],
  devServer: {
    port: 3000,
    proxy: [
      {
        context: ['/'],
        target: 'http://django:8000',
      },
    ],
    client: {
      overlay: {
        errors: true,
        warnings: false,
        runtimeErrors: true,
      },
    },
    // We need hot=false (Disable HMR) to set liveReload=true
    hot: false,
    liveReload: true,
  },
});

import * as echarts from 'echarts';

let elevationChart = echarts.init(document.getElementById('elevation_chart'));

let options = {
  title: {
    text: 'Average elevation of surrounding land',
    textAlign: 'centre',
  },
  tooltip: {
    trigger: 'axis',
    valueFormatter: (value) => `${value} m`,
    // formatter: function formatter(params) {
    //    console.log(params);
    //    return `${params.seriesName}<br>${params.marker} ${params.value}: ${params.value[params.seriesName]}`;
    // }
  },
  xAxis: {
    name: 'Distance from source',
    nameLocation: 'center',
    nameGap: 25,
    axisLabel: {
      formatter: '{value} km',
    },
  },
  yAxis: {
    name: 'Elevation (m)',
    axisLabel: {
      formatter: '{value} m',
    },
  },
  series: [
    {
      data: [],
      name: 'Elevation',
      smooth: false,
      symbol: function (value, params) {
        'use strict';
        if (params.dataIndex % 10 === 0) {
          return 'arrow';
        }
        return 'none';
      },
      symbolRotate: function (value, params) {
        'use strict';
        // Calculate the angle of the arrow based on the slope of the line.
        if (params.dataIndex < options.series[0].data.length - 1) {
          let nextValue = options.series[0].data[params.dataIndex + 1];

          if (value === undefined || nextValue === undefined) {
            return 0;
          }

          let [xMin, xMax] = elevationChart
            .getModel()
            .getComponent('xAxis')
            .axis.scale.getExtent();
          let [yMin, yMax] = elevationChart
            .getModel()
            .getComponent('yAxis')
            .axis.scale.getExtent();

          let xScale = xMax - xMin;
          let yScale = yMax - yMin;

          let width = elevationChart.getWidth();
          let height = elevationChart.getHeight();

          let deltaY = nextValue[1] - value[1];
          let deltaX = nextValue[0] - value[0];

          let normalizedDeltaX = (deltaX / xScale) * width;
          let normalizedDeltaY = (deltaY / yScale) * height;

          let angle =
            (Math.atan2(normalizedDeltaY, normalizedDeltaX) * 180) / Math.PI;

          // Rotate 90 degrees clockwise to take into account the fact the arrow is pointing upwards at 0 rotation.
          angle = angle - 90;
          return angle;
        }

        return 0;
      },
      symbolSize: 7,
      type: 'line',
    },
  ],
};

elevationChart.setOption(options);

const osm_id = document.querySelector('#osm_id').innerText;
fetch(`/rivers/${osm_id}/elevation_data/`)
  .then((response) => response.json())
  .then((data) => {
    'use strict';

    options.series[0].data = data;
    elevationChart.setOption(options);
  });

#!/usr/bin/env node
/**
 * 既知の誤ジオコーディングを施設の検証済み基準点へ統一する。
 *
 * 基準点（OpenStreetMap / Nominatim で 2026-07-16 に照合）:
 * - Jヴィレッジ: 37.2419456, 141.0075299
 * - 天神岬スポーツ公園: 37.2672453, 141.0135195
 *
 * 自由文から生成された従来の座標には、同一施設なのに沖合・別地点へ
 * 分散した値があった。施設名が spot に明示されたレコードだけを対象にする。
 */
const fs = require('fs');

const LANDMARKS = [
  {
    name: 'Jヴィレッジ',
    pattern: /J[-－ ]?VILLAGE|J[-－ ]?ヴィレッジ|Jウ.?ィレッジ/i,
    lat: 37.241946,
    lon: 141.00753,
  },
  {
    name: '天神岬スポーツ公園',
    pattern: /天神岬/,
    lat: 37.267245,
    lon: 141.01352,
  },
];

// 施設名は分かるが、撮影地点が海岸・周辺などにとどまるものは
// 施設の代表点として明示し、初期表示の精密地点からは除外する。
const APPROXIMATE_SPOT = /海岸|護岸|周辺|付近|から|方向/;

let changed = 0;
for (let n = 1; n <= 4; n++) {
  const path = `disaster_data_${n}.js`;
  const original = fs.readFileSync(path, 'utf8');
  const prefix = original.slice(0, original.indexOf('['));
  const data = JSON.parse(original.slice(original.indexOf('[')).replace(/;\s*$/, ''));
  let fileChanged = false;

  for (const item of data) {
    const spot = item.spot || '';
    const landmark = LANDMARKS.find(candidate => candidate.pattern.test(spot));
    if (!landmark || !item.has_coord) continue;

    if (item.lat !== landmark.lat || item.lon !== landmark.lon) {
      item.lat = landmark.lat;
      item.lon = landmark.lon;
      item.loc_precision = APPROXIMATE_SPOT.test(spot) ? 'area-centroid' : 'building';
      fileChanged = true;
      changed++;
    }
  }

  if (fileChanged) fs.writeFileSync(path, `${prefix}${JSON.stringify(data)};`);
}

console.log(`Corrected ${changed} records.`);

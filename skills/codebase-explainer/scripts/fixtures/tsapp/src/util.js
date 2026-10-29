const fs = require('fs');
const math = require('./lib/math');

module.exports = { read: (p) => fs.readFileSync(p), math };

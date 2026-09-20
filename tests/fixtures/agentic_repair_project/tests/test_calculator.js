const assert = require("assert");
const { add } = require("../src/calculator");

assert.strictEqual(add(2, 3), 5, "add(2,3) should equal 5");
console.log("ok");

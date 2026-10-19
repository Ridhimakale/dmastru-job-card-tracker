const RS_BLOCKS: Record<number, { total: number; data: number }[]> = {
  1: [{ total: 26, data: 19 }],
  2: [{ total: 44, data: 34 }],
  3: [{ total: 70, data: 55 }],
  4: [{ total: 100, data: 80 }],
  5: [{ total: 134, data: 108 }],
  6: [{ total: 86, data: 68 }, { total: 86, data: 68 }],
  7: [{ total: 98, data: 78 }, { total: 98, data: 78 }],
  8: [{ total: 121, data: 97 }, { total: 121, data: 97 }],
  9: [{ total: 146, data: 116 }, { total: 146, data: 116 }],
  10: [{ total: 86, data: 68 }, { total: 86, data: 68 }, { total: 87, data: 69 }, { total: 87, data: 69 }],
};
const DATA_CODEWORDS: Record<number, number> = { 1: 19, 2: 34, 3: 55, 4: 80, 5: 108, 6: 136, 7: 156, 8: 194, 9: 232, 10: 274 };
const ALIGNMENT: Record<number, number[]> = {
  1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
  7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
};

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
for (let i = 0, value = 1; i < 255; i++) {
  EXP[i] = value; LOG[value] = i; value <<= 1;
  if (value & 0x100) value ^= 0x11d;
}
for (let i = 255; i < EXP.length; i++) EXP[i] = EXP[i - 255];

function multiply(a: number, b: number): number {
  return a && b ? EXP[LOG[a] + LOG[b]] : 0;
}
function bitLength(value: number): number {
  let length = 0;
  while (value) { length++; value >>>= 1; }
  return length;
}
function bch(value: number, polynomial: number): number {
  let remainder = value << (bitLength(polynomial) - 1);
  while (bitLength(remainder) >= bitLength(polynomial)) remainder ^= polynomial << (bitLength(remainder) - bitLength(polynomial));
  return (value << (bitLength(polynomial) - 1)) | remainder;
}
function reedSolomon(data: number[], degree: number): number[] {
  let generator = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array<number>(generator.length + 1).fill(0);
    generator.forEach((coefficient, index) => {
      next[index] ^= coefficient;
      next[index + 1] ^= multiply(coefficient, EXP[i]);
    });
    generator = next;
  }
  const work = [...data, ...new Array<number>(degree).fill(0)];
  for (let i = 0; i < data.length; i++) {
    const factor = work[i];
    if (factor) generator.forEach((coefficient, index) => { work[i + index] ^= multiply(coefficient, factor); });
  }
  return work.slice(data.length);
}
function pushBits(target: number[], value: number, count: number) {
  for (let bit = count - 1; bit >= 0; bit--) target.push((value >>> bit) & 1);
}
function codewords(bytes: Uint8Array, version: number): number[] | null {
  const countBits = version < 10 ? 8 : 16;
  if (bytes.length >= 2 ** countBits) return null;
  const bits: number[] = [];
  pushBits(bits, 0b0100, 4);
  pushBits(bits, bytes.length, countBits);
  bytes.forEach(byte => pushBits(bits, byte, 8));
  const capacity = DATA_CODEWORDS[version] * 8;
  for (let i = 0; i < Math.min(4, capacity - bits.length); i++) bits.push(0);
  while (bits.length % 8) bits.push(0);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((value, bit) => (value << 1) | bit, 0));
  for (let pad = 0; data.length < DATA_CODEWORDS[version]; pad++) data.push(pad % 2 ? 0x11 : 0xec);

  const blocks: { data: number[]; ec: number[] }[] = [];
  let offset = 0;
  for (const spec of RS_BLOCKS[version]) {
    const blockData = data.slice(offset, offset + spec.data); offset += spec.data;
    blocks.push({ data: blockData, ec: reedSolomon(blockData, spec.total - spec.data) });
  }
  const result: number[] = [];
  for (let i = 0; i < Math.max(...blocks.map(block => block.data.length)); i++) blocks.forEach(block => { if (i < block.data.length) result.push(block.data[i]); });
  for (let i = 0; i < Math.max(...blocks.map(block => block.ec.length)); i++) blocks.forEach(block => { if (i < block.ec.length) result.push(block.ec[i]); });
  return result;
}

export function createQrMatrix(value: string): boolean[][] | null {
  const bytes = new TextEncoder().encode(value);
  const version = Object.keys(DATA_CODEWORDS).map(Number).find(candidate => {
    const countBits = candidate < 10 ? 8 : 16;
    return bytes.length < 2 ** countBits && 4 + countBits + bytes.length * 8 <= DATA_CODEWORDS[candidate] * 8;
  });
  if (!version) return null;
  const words = codewords(bytes, version);
  if (!words) return null;
  const size = version * 4 + 17;
  const matrix = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const fixed = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const setFixed = (x: number, y: number, dark: boolean) => {
    if (x >= 0 && y >= 0 && x < size && y < size) { matrix[y][x] = dark; fixed[y][x] = true; }
  };
  const finder = (cx: number, cy: number) => {
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const edge = Math.abs(dx) <= 3 && Math.abs(dy) <= 3 && (Math.abs(dx) === 3 || Math.abs(dy) === 3);
      const center = Math.abs(dx) <= 1 && Math.abs(dy) <= 1;
      setFixed(cx + dx, cy + dy, edge || center);
    }
  };
  finder(3, 3); finder(size - 4, 3); finder(3, size - 4);
  for (let i = 8; i < size - 8; i++) {
    if (!fixed[6][i]) setFixed(i, 6, i % 2 === 0);
    if (!fixed[i][6]) setFixed(6, i, i % 2 === 0);
  }
  for (const cy of ALIGNMENT[version]) for (const cx of ALIGNMENT[version]) {
    if ((cx === 6 && cy === 6) || (cx === size - 7 && cy === 6) || (cx === 6 && cy === size - 7)) continue;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      setFixed(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }

  const format = bch(0b01000, 0x537) ^ 0x5412;
  for (let i = 0; i < 15; i++) {
    const bit = ((format >>> i) & 1) !== 0;
    if (i < 6) setFixed(8, i, bit);
    else if (i < 8) setFixed(8, i + 1, bit);
    else if (i === 8) setFixed(7, 8, bit);
    else setFixed(14 - i, 8, bit);
    if (i < 8) setFixed(size - 1 - i, 8, bit);
    else setFixed(8, size - 15 + i, bit);
  }
  setFixed(8, size - 8, true);
  if (version >= 7) {
    const versionBits = bch(version, 0x1f25);
    for (let i = 0; i < 18; i++) {
      const bit = ((versionBits >>> i) & 1) !== 0;
      const a = size - 11 + i % 3, b = Math.floor(i / 3);
      setFixed(a, b, bit); setFixed(b, a, bit);
    }
  }

  const dataBits = words.flatMap(word => Array.from({ length: 8 }, (_, i) => (word >>> (7 - i)) & 1));
  let bitIndex = 0, upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right--;
    for (let vertical = 0; vertical < size; vertical++) {
      const y = upward ? size - 1 - vertical : vertical;
      for (let column = 0; column < 2; column++) {
        const x = right - column;
        if (fixed[y][x]) continue;
        const bit = bitIndex < dataBits.length ? dataBits[bitIndex++] !== 0 : false;
        matrix[y][x] = bit !== ((x + y) % 2 === 0);
      }
    }
    upward = !upward;
  }
  return matrix;
}

export function qrSvgPath(matrix: boolean[][]): string {
  const commands: string[] = [];
  matrix.forEach((row, y) => row.forEach((dark, x) => { if (dark) commands.push(`M${x},${y}h1v1h-1z`); }));
  return commands.join('');
}

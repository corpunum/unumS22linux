// A small JSON Schema checker for the subset s22d's /v1/capabilities uses:
// type, enum, minimum/maximum, minLength/maxLength, required, additionalProperties:false.
// The plugin checks input before calling the daemon, and the contract tests use the same
// checker to play the daemon's side.

function typeOk(type, value) {
  switch (type) {
    case 'string': return typeof value === 'string';
    case 'integer': return Number.isInteger(value);
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'boolean': return typeof value === 'boolean';
    case 'object': return value !== null && typeof value === 'object' && !Array.isArray(value);
    default: return true;
  }
}

function checkValue(name, spec, value, errors) {
  if (!typeOk(spec.type, value)) {
    errors.push(`${name} must be ${spec.type}`);
    return;
  }
  if (spec.enum && !spec.enum.includes(value)) errors.push(`${name} must be one of ${spec.enum.join(', ')}`);
  if (typeof value === 'number') {
    if (spec.minimum !== undefined && value < spec.minimum) errors.push(`${name} must be >= ${spec.minimum}`);
    if (spec.maximum !== undefined && value > spec.maximum) errors.push(`${name} must be <= ${spec.maximum}`);
  }
  if (typeof value === 'string') {
    if (spec.minLength !== undefined && value.length < spec.minLength) errors.push(`${name} is shorter than ${spec.minLength}`);
    if (spec.maxLength !== undefined && value.length > spec.maxLength) errors.push(`${name} is longer than ${spec.maxLength}`);
  }
}

/** Returns a list of problems; an empty list means the input is valid. */
export function validate(schema, input) {
  const errors = [];
  if (!typeOk('object', input)) return ['input must be an object'];
  const props = schema.properties || {};
  for (const key of schema.required || []) {
    if (input[key] === undefined) errors.push(`${key} is required`);
  }
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    if (!props[key]) {
      if (schema.additionalProperties === false) errors.push(`${key} is not a known parameter`);
      continue;
    }
    checkValue(key, props[key], value, errors);
  }
  return errors;
}

/** A minimal valid input for a schema, for contract tests. */
export function sampleInput(schema) {
  const out = {};
  for (const key of schema.required || []) {
    const spec = schema.properties[key];
    if (spec.enum) out[key] = spec.enum[0];
    else if (spec.type === 'integer') out[key] = Math.max(spec.minimum ?? 1, 1);
    else if (spec.type === 'number') out[key] = Math.max(spec.minimum ?? 1, 1);
    else if (spec.type === 'boolean') out[key] = true;
    else out[key] = 'x'.repeat(Math.max(spec.minLength ?? 1, 1));
  }
  return out;
}

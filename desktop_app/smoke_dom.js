function applyControlledInputValue(node, value) {
  if (!node) {
    throw new Error("controlled input node missing");
  }

  const nextValue = String(value ?? "");
  const view = node.ownerDocument && node.ownerDocument.defaultView
    ? node.ownerDocument.defaultView
    : globalThis;
  const InputCtor = view.HTMLInputElement || globalThis.HTMLInputElement;
  const TextareaCtor = view.HTMLTextAreaElement || globalThis.HTMLTextAreaElement;
  const isTextarea = Boolean(TextareaCtor) && node instanceof TextareaCtor;
  const prototypeRef = isTextarea
    ? (TextareaCtor && TextareaCtor.prototype)
    : (InputCtor && InputCtor.prototype);
  const valueDescriptor = prototypeRef
    ? Object.getOwnPropertyDescriptor(prototypeRef, "value")
    : null;

  if (valueDescriptor && typeof valueDescriptor.set === "function") {
    valueDescriptor.set.call(node, nextValue);
  } else {
    node.value = nextValue;
  }

  const InputEventCtor = view.InputEvent || globalThis.InputEvent;
  if (typeof InputEventCtor === "function") {
    node.dispatchEvent(new InputEventCtor("input", {
      bubbles: true,
      composed: true,
      data: nextValue,
      inputType: "insertText",
    }));
  } else {
    node.dispatchEvent(new view.Event("input", { bubbles: true, composed: true }));
  }
  node.dispatchEvent(new view.Event("change", { bubbles: true, composed: true }));

  return nextValue;
}

module.exports = {
  applyControlledInputValue,
};

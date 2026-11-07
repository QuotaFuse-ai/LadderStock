/**
 * 测试环境的公共替身。
 *
 * jsdom 不带 2D 画布：任何渲染 `<canvas>` 的组件在 effect 里 `getContext('2d')`
 * 都会让 jsdom 往 stderr 打一条 "Not implemented"（不是失败，但会淹没真实报错）。
 * 这里统一返回 null，组件按「画不出来」那条真实分支走；画布内容本身要在
 * 单元测试里断言的，用录制的假 context（见 `tests/share-card.test.ts`）。
 */

if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = (() =>
    null) as unknown as HTMLCanvasElement['getContext']
}

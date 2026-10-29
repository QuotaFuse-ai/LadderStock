/**
 * PostCSS：Tailwind CSS 3 + Autoprefixer。
 *
 * 固定 Tailwind 3（不是 4）：DESIGN-SPEC §2.12 的 `theme.extend` + `darkMode: ['class', attr]`
 * 都是 v3 语义；v4 换成 CSS-first 配置，与令牌生成脚本不兼容。
 */
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}

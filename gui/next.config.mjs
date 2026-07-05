/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle for the Docker image (deploy/)
  output: "standalone",
};

export default nextConfig;
